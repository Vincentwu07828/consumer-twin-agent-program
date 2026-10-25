import hashlib
import json
import platform
from pathlib import Path
import warnings

import joblib
import numpy as np
import sklearn
from sklearn.exceptions import ConvergenceWarning
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss, brier_score_loss
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .data import label, fingerprint, unique_rows, inspect_rows
from .features import FeatureAdapter, FeatureSpec, token
from .storage import utc_now


def metrics(labels, probabilities):
    labels, probabilities = np.asarray(labels), np.asarray(probabilities)
    if not len(labels):
        return {"rows": 0, "roc_auc": None, "pr_auc": None, "log_loss": None,
                "brier_score": None, "calibration_bins": []}
    both = len(set(labels.tolist())) == 2
    bins = []
    for index in range(10):
        mask = (probabilities >= index / 10) & (
            probabilities <= 1 if index == 9 else probabilities < (index + 1) / 10)
        if mask.any():
            bins.append({"lower": index / 10, "upper": (index + 1) / 10,
                "rows": int(mask.sum()), "mean_probability": float(probabilities[mask].mean()),
                "observed_rate": float(labels[mask].mean())})
    ece = sum(b["rows"] * abs(b["mean_probability"] - b["observed_rate"]) for b in bins) / len(labels)
    return {"rows": len(labels), "roc_auc": float(roc_auc_score(labels, probabilities)) if both else None,
        "pr_auc": float(average_precision_score(labels, probabilities)) if both else None,
        "log_loss": float(log_loss(labels, probabilities, labels=[0, 1])),
        "brier_score": float(brier_score_loss(labels, probabilities)),
        "ece_10_bins": float(ece), "calibration_bins": bins,
        "single_class": not both}


def split_training(rows, seed):
    if any(row.get("time_stamp") is not None for row in rows) and not all(
            row.get("time_stamp") is not None for row in rows):
        raise ValueError("时间字段部分缺失，不能静默改为随机划分")
    if all(row.get("time_stamp") is not None for row in rows):
        try:
            timestamps = [float(row["time_stamp"]) for row in rows]
            if not all(np.isfinite(timestamps)):
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError("time_stamp 必须为有效数值") from None
        distinct = sorted(set(timestamps))
        if len(distinct) < 10:
            raise ValueError("时间分组不足，无法建立独立训练、校准、验证窗口")
        cut_train = distinct[max(1, int(len(distinct) * .8))]
        cut_cal = distinct[max(2, int(len(distinct) * .9))]
        train = [r for r in rows if float(r["time_stamp"]) < cut_train]
        calibration = [r for r in rows if cut_train <= float(r["time_stamp"]) < cut_cal]
        validation = [r for r in rows if float(r["time_stamp"]) >= cut_cal]
        method = "chronological_80_10_10_no_timestamp_group_overlap"
    else:
        train, rest = train_test_split(rows, test_size=.2, random_state=seed,
                                      stratify=[label(r) for r in rows])
        calibration, validation = train_test_split(rest, test_size=.5, random_state=seed,
                                                   stratify=[label(r) for r in rest])
        method = "stratified_80_10_10_no_trusted_timestamp"
    for name, subset in (("train", train), ("calibration", calibration), ("validation", validation)):
        if len({label(r) for r in subset}) != 2:
            raise ValueError(f"{name} 划分必须同时包含点击和未点击，请增加样本")
    return train, calibration, validation, method


def fit_model(train, calibration, validation, test, spec, seed):
    adapter = FeatureAdapter(spec)
    features = lambda rows: [adapter.from_row(row)[0] for row in rows]
    pipeline = Pipeline([
        ("vectorizer", DictVectorizer(sparse=True)),
        ("scaler", StandardScaler(with_mean=False)),
        ("classifier", LogisticRegression(solver="liblinear", max_iter=1000, random_state=seed)),
    ])
    # Encoder, scaler and classifier see training rows only.
    with warnings.catch_warnings():
        warnings.simplefilter("error", ConvergenceWarning)
        pipeline.fit(features(train), [label(r) for r in train])
        calibrator = LogisticRegression(solver="lbfgs", random_state=seed)
        calibrator.fit(pipeline.decision_function(features(calibration)).reshape(-1, 1),
                       [label(r) for r in calibration])
    predict = lambda rows: calibrator.predict_proba(pipeline.decision_function(features(rows)).reshape(-1, 1))[:, 1]
    valid_p, test_p = predict(validation), predict(test)
    return pipeline, calibrator, valid_p, test_p


def train_behavior(train_rows: list[dict], test_rows: list[dict], output_dir: str | Path, *,
                   data_version: str, seed: int = 42, synthetic: bool = False) -> dict:
    if not data_version:
        raise ValueError("必须填写可追溯的数据版本")
    for row in train_rows + test_rows:
        label(row)
    train_rows = unique_rows(train_rows)
    test_rows = unique_rows(test_rows)
    overlap = {fingerprint(r) for r in train_rows} & {fingerprint(r) for r in test_rows}
    if overlap:
        raise ValueError("训练与测试存在完全相同记录；检查划分或重复曝光来源后再训练")
    train, calibration, validation, method = split_training(train_rows, seed)
    if method.startswith("chronological"):
        try:
            earliest_test = min(float(r["time_stamp"]) for r in test_rows)
            if earliest_test <= max(float(r["time_stamp"]) for r in train_rows):
                raise ValueError
        except (KeyError, ValueError, TypeError):
            raise ValueError("时间划分要求官方测试窗口严格晚于训练窗口") from None
    if not test_rows:
        raise ValueError("测试数据为空")
    test_y, valid_y = [label(r) for r in test_rows], [label(r) for r in validation]
    ctr = sum(label(r) for r in train) / len(train)
    report = {"task": "click", "data_version": data_version, "seed": seed,
        "created_at": utc_now(), "synthetic": synthetic,
        "split_method": method, "rows": {"train": len(train), "calibration": len(calibration),
                                          "validation": len(validation), "test": len(test_rows)},
        "versions": {"python": platform.python_version(), "sklearn": sklearn.__version__},
        "baseline_ctr": ctr,
        "baseline": {"validation": metrics(valid_y, np.full(len(validation), ctr)),
                     "test": metrics(test_y, np.full(len(test_rows), ctr))},
        "models": {}, "data_inspection": {"train": inspect_rows(train_rows), "test": inspect_rows(test_rows)},
        "limitations": ["有界前缀样本可能有排序偏差；需要在完整留出集上重新评价",
            "官方划分来源由调用者声明，文件名本身不能证明数据边界",
            "无逐条历史时间戳，历史无未来信息尚未验证",
            "校准集和模型选择验证集独立；测试集不参与预处理、校准或模型选择",
            "PR-AUC 使用 average precision；单类别子集 AUC 为空",
            "新用户、演示商品及缺失属性均需要本地数据校准"]}
    full_bundle = None
    train_users = {token(r.get("userid")) for r in train} - {None}
    train_items = {token(r.get("adgroup_id")) for r in train} - {None}
    for name, profile, history in (("full", True, True), ("without_profile", False, True),
                                   ("without_history", True, False)):
        spec = FeatureSpec(include_profile=profile, include_history=history)
        pipeline, calibrator, valid_p, test_p = fit_model(train, calibration, validation, test_rows, spec, seed)
        cold = {}
        for group, key, seen in (("unseen_user", "userid", train_users), ("unseen_item", "adgroup_id", train_items)):
            mask = np.asarray([token(r.get(key)) not in seen for r in test_rows])
            cold[group] = metrics(np.asarray(test_y)[mask], test_p[mask])
        report["models"][name] = {"feature_spec": spec.to_dict(), "validation": metrics(valid_y, valid_p),
            "test": metrics(test_y, test_p), "cold_start": cold}
        if name == "full":
            digest = hashlib.sha256((data_version + str(seed) + json.dumps(spec.to_dict())).encode()).hexdigest()[:12]
            full_bundle = {"artifact_version": 1, "task": "click", "model_version": f"lr-click-v1-{digest}",
                "pipeline": pipeline, "calibrator": calibrator, "feature_spec": spec.to_dict(),
                "baseline_ctr": ctr, "data_version": data_version,
                "scope": "synthetic_smoke_test_only" if synthetic else "taobaoad_offline_experimental",
                "validation_improved": report["models"][name]["validation"]["log_loss"] < report["baseline"]["validation"]["log_loss"],
                "versions": report["versions"]}
    report["validation_improved"] = full_bundle["validation_improved"]
    report["deployment_validated"] = False
    report["runtime_behavior"] = "calibrated_logistic_regression" if full_bundle["validation_improved"] else "constant_ctr_fallback"
    report["model_version"] = full_bundle["model_version"]
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(full_bundle, output_dir / "behavior.joblib")
    (output_dir / "feature_spec.json").write_text(json.dumps(full_bundle["feature_spec"], ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "evaluation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return report
