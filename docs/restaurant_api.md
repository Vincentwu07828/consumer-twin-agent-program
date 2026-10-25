# 餐厅推荐后端接口

当前主线：问卷事实 → LifeSim 原生画像与记忆 → 用餐需求确认 → 真实餐厅硬条件筛选 → DeepSeek 推荐顺序与理由 → 用户明确反馈。无需先训练点击模型。前端网页和 3D 形象在下一步制作。

启动本地后端：

```powershell
.\.venv\Scripts\python.exe scripts/serve_restaurant_agent.py
```

地址为 `http://127.0.0.1:8000`。这是本地开发接口，允许本地 5173 端口的前端访问。会话由后端生成，不接受前端指定其他用户的画像 ID；前端保留返回的 session_id。密钥只在后端使用。

## 接口

| 方法和路径 | 输入 | 输出／用途 |
|---|---|---|
| GET `/api/health` | 无 | 服务健康状态，不调用 LLM |
| GET `/api/questionnaire` | 无 | 七个问卷字段及类型，不调用 LLM |
| POST `/api/sessions` | answers、consent | session_id、已知问卷画像；空项保持未知 |
| GET `/api/profile?session_id=…` | session_id | 当前画像 |
| POST `/api/intent` | session_id、now、last_meal_at、explicit_hunger | 用餐提示及是否需要确认 |
| POST `/api/recommendations` | session_id、need、context、candidates（可选） | 推荐餐厅卡片、理由、筛除原因 |
| POST `/api/feedback` | session_id、run_id、selected_id、satisfaction、rejected_reasons、comment | 明确反馈入库和 LifeSim 记忆更新 |
| DELETE `/api/session` | session_id | 删除画像、轨迹、反馈及原生记忆索引 |

所有 POST／DELETE 使用 JSON 请求体。问题字段支持：preferred_cuisines、meal_budget_cny、excluded_ingredients、allergies、required_diet_tags、max_distance_meters、usual_dinner_time。缺失字段不会自动补为事实；空列表表示用户明确选择“无”，跳过未知字段时应省略或传 null。

## 问卷与意图

```json
{
  "answers": {
    "preferred_cuisines": ["川菜"],
    "meal_budget_cny": 50,
    "allergies": ["花生"],
    "excluded_ingredients": ["香菜"],
    "max_distance_meters": 1500,
    "usual_dinner_time": "18:00"
  },
  "consent": true
}
```

晚上六点仅产生 `possible_dining` 和 `requires_confirmation=true`，不会标成确定饥饿。用户明确确认后才进入主动推荐；最近刚吃过饭时，不按时间自动提示。当前规则用于产品流程，尚未用行为数据验证饥饿预测。

## 餐厅候选

候选可以来自地图／餐厅数据接口，或用户有来源的本地目录。默认目录 `data/restaurants/catalog.json` 为空，不把虚构餐厅包装成真实餐厅。前端将真实来源的候选传入 candidates，或后续接入 CandidateProvider 即可；这次尚未接入地图服务。

候选字段如下，示例值仅用于说明协议：

```json
{
  "candidate_id": "source-restaurant-id",
  "name": "由实际来源提供的餐厅名称",
  "category_id": "restaurant",
  "source": "地图接口或用户提供的目录来源",
  "price_cny": 45,
  "price_basis": "per_person_estimate",
  "cuisines": ["川菜"],
  "address": "实际来源提供的地址",
  "distance_meters": 800,
  "is_open": true,
  "ingredients": ["牛肉", "辣椒"],
  "allergens": [],
  "allergen_info_verified": true,
  "diet_tags": []
}
```

价格使用人民币／人；人均估价会作为 warning 返回，不能当成保证结账金额。食材／过敏原／饮食要求信息不知道时用 null，不能填空数组冒充已核实没有。用户有明确过敏禁忌时，未核实相关信息的餐厅会被排除；距离上限已指定而距离未知也会排除。餐厅闭店、超预算或违反禁忌的候选不能由 LLM 加回来。菜系偏好为排序因素，不要求其他菜系都被排除。

## 请求推荐

```json
{
  "session_id": "问卷接口返回的值",
  "need": "想吃一顿适合我口味的晚餐",
  "context": {
    "budget_cny": 50,
    "time": "2026-10-14T18:00:00+08:00",
    "location": "用户提供的位置",
    "max_distance_meters": 1500
  },
  "candidates": []
}
```

填写 candidates 时使用上述真实候选对象。context 支持预算、位置、带时区时间、距离、菜系及饮食要求；当前请求不能静默清除问卷确认的禁忌。如果没有明确需求且没有确认饥饿，返回 `needs_confirmation`；预算未知返回 `needs_budget`；没有可行候选返回 `no_candidates`，不会让 LLM 虚构餐厅。

成功返回的 recommendations 每项包含 rank、restaurant、reason、warnings。完整运行记录在 SQLite，前端显示卡片即可，不需要显示模型实现细节。推荐顺序和理由不表示购买概率或真实消费者动机。

## 反馈

```json
{
  "session_id": "问卷接口返回的值",
  "run_id": "推荐接口返回的值",
  "selected_id": "本次实际展示的餐厅编号",
  "satisfaction": 4,
  "rejected_reasons": {"另一个实际展示的编号": "今天不想吃这个口味"},
  "comment": "用户自己的反馈"
}
```

只能反馈本次展示的候选；同次运行只提交一次完整反馈。没有报告选择或拒绝，不补成负例。选择餐厅不自动表示已经就餐或支付，不扣预算。用户反馈带餐厅名称、菜系和来源进入 LifeSim KVMemory，下一次推荐会检索；真实选择记录可以为未来训练排序／选择模型积累数据。
