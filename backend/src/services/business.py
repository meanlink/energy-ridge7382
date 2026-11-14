"""Time-ordered ridge regression with nested feature search and load scenarios."""
from datetime import date, timedelta
from itertools import combinations
import math
import random
import numpy as np
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_squared_error, r2_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


FEATURES = {"units": "产量", "run_hours": "运行小时", "temp_c": "环境温度", "idle_hours": "待机小时"}


def metadata():
    return {
        "name": "车间能耗驱动归因", "short": "energy-ridge", "tagline": "用时间留出验证解释能耗驱动，并试算生产负荷变化",
        "client": "模拟客户：澄川金属一号冲压车间，三班制，6 条冲压线。",
        "workflow": "能源专员把日抄表与排产表拼接，按吨产品单耗判断异常；设备组按经验解释高耗日期。",
        "pains": ["产量和开机小时共线，单因素排名容易误导", "月底全量拟合无法证明外推能力", "调产讨论缺少量化情景和参数记录"],
        "upstream": "每日线体抄表、生产工时与车间温度记录", "downstream": "能源专员与排产组的下周负荷评审。",
        "entities": ["能耗日样本", "归因试验", "负荷简报计划"], "dataset_name": "澄川_冲压一线_160日能耗.csv",
        "columns": [{"key": "day", "label": "日期", "type": "string"}, {"key": "line", "label": "线体", "type": "string"}, {"key": "units", "label": "日产量", "type": "number"}, {"key": "run_hours", "label": "运行小时", "type": "number"}, {"key": "temp_c", "label": "温度℃", "type": "number"}, {"key": "idle_hours", "label": "待机小时", "type": "number"}, {"key": "kwh", "label": "耗电kWh", "type": "number"}],
        "parameters": [{"key": "max_features", "label": "最多解释变量", "type": "number", "default": 4, "min": 2, "max": 4}, {"key": "scenario_pct", "label": "情景负荷变化 %", "type": "number", "default": 10, "min": -30, "max": 30}],
        "workbench_title": "特征筛选与负荷情景", "action_label": "训练并验证能耗模型", "unit": "kWh", "accent": "oklch(49% 0.11 145)", "style": "energy-sheet",
        "seed_story": ["08:10 导入澄川_冲压一线_160日能耗.csv。", "08:20 核对冲压 P01 的产量、运行小时和独立电表读数。", "09:00 允许最多 4 个解释变量，保留最后 20% 日期作验证。", "09:15 查看时间序列交叉验证选出的变量与岭惩罚。", "09:30 评估实际留出期 RMSE，不用训练误差替代。", "10:00 试算负荷增加 10% 的平均日电耗。", "14:00 将系数解释和验证误差交给排产组，设置例会简报计划。"],
        "method": "前80%日期训练；训练内3折前向时间序列交叉验证搜索2至k个变量组合和α∈{0.1,1,10}。每折独立标准化，最后20%仅验证一次。",
        "limitations": ["回归系数反映条件关联，不能证明因果。", "情景仅同步缩放近期产量和运行小时，不涵盖设备启停、峰谷电价与极端负荷。"],
    }


def seed_records():
    rng = random.Random(1016)
    start = date(2026, 5, 1)
    rows = []
    for i in range(160):
        units = round(760 + 180 * math.sin(i / 11) + rng.gauss(0, 85), 1)
        run_hours = round(6.4 + units / 140 + rng.gauss(0, 0.65), 2)
        temp = round(24 + 7 * math.sin(i / 33) + rng.gauss(0, 1.4), 2)
        idle = round(rng.uniform(0.3, 3.6), 2)
        kwh = round(125 + units * 0.46 + run_hours * 24 + temp * 4.1 + idle * 38 + rng.gauss(0, 13), 2)
        rows.append({"day": str(start + timedelta(days=i)), "line": "冲压P01", "units": units, "run_hours": run_hours, "temp_c": temp, "idle_hours": idle, "kwh": kwh})
    return rows


def _bounded(value, name, low, high):
    try:
        value = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"{name}必须为数值") from None
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name}必须介于{low}和{high}")
    return value


def analyze(records, params):
    if not isinstance(records, list) or len(records) < 70:
        raise ValueError("至少需要70天能耗记录，以保留时间验证窗")
    k = _bounded(params.get("max_features", 4), "解释变量数", 2, 4)
    if not k.is_integer():
        raise ValueError("解释变量数必须为整数")
    delta = _bounded(params.get("scenario_pct", 10), "情景负荷变化", -30, 30)
    parsed = []
    for item in records:
        try:
            day = date.fromisoformat(str(item["day"]))
            vector = [float(item[key]) for key in FEATURES]
            target = float(item["kwh"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("日期须为ISO日期，4个特征及kwh必须是数值") from None
        if not all(math.isfinite(value) for value in vector + [target]):
            raise ValueError("数值不能包含缺失、NaN或无穷")
        if not (0 <= vector[0] <= 10_000_000 and 0 <= vector[1] <= 24 and -100 <= vector[2] <= 100 and 0 <= vector[3] <= 24 and 0 <= target <= 1_000_000_000):
            raise ValueError("日产量须在0至1000万，运行/待机小时0至24，温度−100至100℃，日电耗0至10亿kWh")
        parsed.append((day, vector, target))
    parsed.sort(key=lambda row: row[0])
    if len({row[0] for row in parsed}) != len(parsed):
        raise ValueError("同一日期必须先聚合为一条记录，避免样本权重失真")
    x = np.asarray([row[1] for row in parsed], dtype=float)
    y = np.asarray([row[2] for row in parsed], dtype=float)
    if float(np.std(y)) < 1e-9:
        raise ValueError("耗电量没有变化，无法辨识能耗驱动")
    cut = int(len(parsed) * 0.8)
    best = None
    for count in range(2, int(k) + 1):
        for features in combinations(range(4), count):
            for alpha in (0.1, 1.0, 10.0):
                errors = []
                for train, valid in TimeSeriesSplit(n_splits=3).split(x[:cut]):
                    model = make_pipeline(StandardScaler(), Ridge(alpha=alpha))
                    model.fit(x[train][:, features], y[train])
                    prediction = model.predict(x[valid][:, features])
                    errors.append(float(mean_squared_error(y[valid], prediction) ** 0.5))
                candidate = (float(np.mean(errors)), features, alpha)
                if best is None or candidate[0] < best[0]:
                    best = candidate
    cv_rmse, features, alpha = best
    model = make_pipeline(StandardScaler(), Ridge(alpha=alpha))
    model.fit(x[:cut][:, features], y[:cut])
    predictions = model.predict(x[cut:][:, features])
    rmse = float(mean_squared_error(y[cut:], predictions) ** 0.5)
    r2_defined = float(np.std(y[cut:])) > 1e-9
    r2 = float(r2_score(y[cut:], predictions)) if r2_defined else None
    scaler = model.named_steps["standardscaler"]
    coefficients = model.named_steps["ridge"].coef_ / scaler.scale_
    baseline = np.mean(x[-14:], axis=0)
    scenario = baseline.copy()
    scenario[0] *= 1 + delta / 100
    scenario[1] *= 1 + delta / 100
    base_kwh = float(model.predict(baseline[list(features)].reshape(1, -1))[0])
    scenario_kwh = float(model.predict(scenario[list(features)].reshape(1, -1))[0])
    rows = [{"feature": FEATURES[list(FEATURES)[index]], "coefficient": round(float(coeff), 4), "baseline": round(float(baseline[index]), 3), "scenario": round(float(scenario[index]), 3)} for index, coeff in zip(features, coefficients)]
    warnings = ["情景超出训练期变量范围，外推风险较高。"] if any(scenario[index] < float(x[:cut, index].min()) or scenario[index] > float(x[:cut, index].max()) for index in features) else []
    if not r2_defined:
        warnings.append("留出期耗电量恒定，R²不可定义，请以RMSE判读验证误差。")
    elif r2 < 0.5:
        warnings.append("留出期解释度偏低，需补充设备状态、产品组合等变量后再做业务决策。")
    if scenario_kwh < 0:
        warnings.append("线性模型给出负的情景日电耗，当前情景超出模型的物理适用范围，需重新建模。")
    return {
        "summary": f"从{len(parsed)}天记录选出{len(features)}个变量，留出{len(parsed)-cut}天RMSE为{rmse:.2f}kWh；负荷变化{delta:+g}%情景日电耗{scenario_kwh:.2f}kWh。",
        "metrics": [{"label": "验证RMSE", "value": round(rmse, 3), "unit": "kWh"}, {"label": "验证R²", "value": round(r2, 4) if r2_defined else "不可定义"}, {"label": "情景日电耗", "value": round(scenario_kwh, 2), "unit": "kWh"}, {"label": "情景差额", "value": round(scenario_kwh-base_kwh, 2), "unit": "kWh"}],
        "table": {"columns": [{"key": "feature", "label": "入选特征"}, {"key": "coefficient", "label": "原始单位系数"}, {"key": "baseline", "label": "近期基准"}, {"key": "scenario", "label": "情景值"}], "rows": rows},
        "chart": {"type": "line", "labels": [str(row[0]) for row in parsed[cut:]], "series": [{"name": "留出期实测", "values": [round(float(value), 3) for value in y[cut:]]}, {"name": "留出期预测", "values": [round(float(value), 3) for value in predictions]}]},
        "findings": [f"选定岭惩罚α={alpha:g}；训练内交叉验证RMSE={cv_rmse:.3f}kWh。", f"最近14天平均特征对应基准日电耗{base_kwh:.2f}kWh，情景差额{scenario_kwh-base_kwh:+.2f}kWh。", "每折的标准化器仅在该折训练部分拟合；最后20%记录未参与变量和α选择。", "系数已还原到原始单位，解释为其他入选变量不变时的条件关联。"],
        "method": metadata()["method"], "warnings": warnings,
    }
