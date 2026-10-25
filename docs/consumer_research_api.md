# 本地研究接口

启动 `scripts/serve_consumer_workbench.py`，仅监听 `http://127.0.0.1:5173`。请求使用JSON。密钥只由后端读取，真实会话存入 `runtime/consumer_research.sqlite3`。

| 方法与路径 | 输入与用途 |
|---|---|
| GET `/api/research/status` | 配置存在性、模型名、最近成功调用时间；不返回密钥 |
| POST `/api/research/nearby` | `longitude, latitude, category`（dining/shopping）；主动授权定位后查询真实周边店铺，响应含地点与数据缺项提示，不要求研究会话 |
| POST `/api/research/sessions` | `consent: true`；可选 purpose为user_research或integration_probe |
| GET `/api/research/session?session_id=...` | 恢复会话，未反馈预测仍隐藏 |
| POST `/api/research/chat` | `session_id, text, request_id`；可选 `nearby_search_id` 引用本机30分钟内的高德地点查询结果，用于真实店铺对话建议 |
| POST `/api/research/profile` | `session_id, proposal_ids`；或updates手动更正，scope为profile/context |
| POST `/api/research/context` | `session_id, context`，支持hour/budget/coupon |
| POST `/api/research/runs` | `session_id, request_id, branch_type`，分支baseline/budget/coupon；可选candidates |
| POST `/api/research/feedback` | `session_id, run_id, selected_id`，可选satisfaction（1–5）、comment；或skip: true |
| GET `/api/research/export?session_id=...` | 研究JSON，未揭示预测继续隐藏 |
| DELETE `/api/research/session` | `session_id`，删除对应会话、画像和记忆 |

画像字段budget/cuisines/excluded/allergies/diet_tags/max_distance/dinner_time，未知为null。提取结果保留当前消息原话，用户确认后进入画像和记忆。

预演先检查预算和饮食硬条件，再调用原生LifeSim模拟用户/助手进行2轮内部交流，DeepSeek输出合法候选中的选择、排名和理由，也可选none。概率为null。模型内部话语不会写入真人反馈记忆。

选择前GET/POST/导出均剔除预测、内部对话和私有快照；反馈后揭示原预测，跳过标签保持未知。预算/券分支固定基线画像、历史和候选，不读取后续反馈。相同request_id可安全重试相同输入。

脚本验证会话purpose为integration_probe，选择来源为integration_probe_choice，不作为人类样本。当前使用虚构研究候选，没有实际交易；正式参与者管理和批量实证评测仍待实施。本服务面向本机单用户，不面向公网。

周边地点查询使用高德 Web 服务 Key：在 `config/secrets.local.json` 填写 `amap_web_service_key`（或设置 `AMAP_WEB_SERVICE_KEY` 环境变量），重启服务。浏览器仅在点击定位按钮后请求权限；后端先按高德接口将浏览器 GPS 坐标转换为高德坐标，再调用周边 POI 搜索。坐标不保存到研究会话或记忆。搜索返回 `search_id`，候选在本机内存中保留30分钟；对话请求携带此编号时，DeepSeek 可以从真实候选中给出建议，回复标明来源。返回的人均价若有也仅作参考；由于缺少已核验的菜单、食材和券适用性，真实地点结果目前不自动提交给 `/api/research/runs` 预演。
