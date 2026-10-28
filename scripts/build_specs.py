"""Генерирует Vega-Lite спеки в dashboard/specs/*.json.
Спеки — обычные JSON-файлы в репозитории; скрипт нужен только чтобы разрезы
по отрасли и программе были гарантированно одинаковыми.
Запуск: python3 scripts/build_specs.py
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "dashboard" / "specs"
SCHEMA = "https://vega.github.io/schema/vega-lite/v5.json"

DATE_COLOR = {
    "field": "date_label", "type": "nominal", "title": "Дата среза",
    "scale": {"domain": ["01.09.2026", "01.10.2026"], "range": ["#a3a29c", "#2a78d6"]},
    "legend": {"orient": "top"},
}
DATE_LABEL = {"calculate": "datum.report_date == '2026-09-01' ? '01.09.2026' : '01.10.2026'",
              "as": "date_label"}
CONFIG = {
    "font": "Inter, system-ui, sans-serif",
    "view": {"stroke": None},
    "axis": {"labelColor": "#52514e", "titleColor": "#52514e", "gridColor": "#ebeae6",
             "domainColor": "#c9c8c2", "tickColor": "#c9c8c2", "labelFontSize": 11},
    "legend": {"labelColor": "#52514e", "titleColor": "#52514e"},
    "title": {"color": "#0b0b0b", "fontSize": 14, "anchor": "start", "fontWeight": 600},
    "bar": {"cornerRadiusEnd": 4},
}


def spec(**kw):
    return {"$schema": SCHEMA, "config": CONFIG, **kw}


def write(name, s):
    (OUT / f"{name}.json").write_text(json.dumps(s, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# 1. KPI-плитки: значение на 01.10 и изменение к 01.09
kpi = spec(
    title="Ключевые метрики на 01.10.2026 и изменение к 01.09.2026",
    data={"url": "data/v_metrics_change.json"},
    transform=[
        {"calculate": "datum.kind == 'amount' ? format(datum.value_2026_10_01 / 1e9, ',.1f') + ' млрд ₸'"
                      " : format(datum.value_2026_10_01, '.2%')", "as": "value_txt"},
        {"calculate": "datum.kind == 'amount' ? (datum.delta_rel >= 0 ? '▲ +' : '▼ ') + format(datum.delta_rel, '.1%')"
                      " : (datum.delta_abs >= 0 ? '▲ +' : '▼ ') + format(datum.delta_abs * 100, '.2f') + ' п.п.'",
         "as": "delta_txt"},
        {"calculate": "datum.kind == 'amount' ? '01.09: ' + format(datum.value_2026_09_01 / 1e9, ',.1f') + ' млрд'"
                      " : '01.09: ' + format(datum.value_2026_09_01, '.2%')", "as": "base_txt"},
    ],
    facet={"field": "label", "type": "nominal", "sort": {"field": "ord"}, "title": None,
           "header": {"labelFontSize": 12, "labelColor": "#52514e", "labelAnchor": "start"}},
    columns=6,
    spec={
        "width": 150, "height": 70,
        "layer": [
            {"mark": {"type": "text", "fontSize": 22, "fontWeight": 600, "align": "left", "x": 0, "y": 16},
             "encoding": {"text": {"field": "value_txt"}, "color": {"value": "#0b0b0b"}}},
            {"mark": {"type": "text", "fontSize": 12, "align": "left", "x": 0, "y": 44},
             "encoding": {"text": {"field": "delta_txt"}, "color": {"value": "#52514e"}}},
            {"mark": {"type": "text", "fontSize": 11, "align": "left", "x": 0, "y": 62},
             "encoding": {"text": {"field": "base_txt"}, "color": {"value": "#8a8984"}}},
        ],
    },
    resolve={"scale": {"x": "independent"}},
)
write("kpi", kpi)

# 2. Динамика долей: dumbbell 01.09 → 01.10 (одна шкала — все метрики в долях портфеля)
dyn = spec(
    title="Динамика риск-метрик: 01.09 → 01.10.2026, доля портфеля",
    width=640, height=200,
    data={"url": "data/v_metrics_change.json"},
    transform=[{"filter": "datum.kind == 'pct'"},
               {"fold": ["value_2026_09_01", "value_2026_10_01"], "as": ["col", "value"]},
               {"calculate": "datum.col == 'value_2026_09_01' ? '01.09.2026' : '01.10.2026'", "as": "date_label"}],
    encoding={"y": {"field": "label", "type": "nominal", "sort": {"field": "ord"}, "title": None},
              "x": {"field": "value", "type": "quantitative", "axis": {"format": ".0%"}, "title": None}},
    layer=[
        {"mark": {"type": "line", "color": "#c9c8c2", "strokeWidth": 2},
         "encoding": {"detail": {"field": "label"}}},
        {"mark": {"type": "point", "filled": True, "size": 110, "opacity": 1},
         "encoding": {"color": DATE_COLOR,
                      "tooltip": [{"field": "label", "title": "Метрика"},
                                  {"field": "date_label", "title": "Дата"},
                                  {"field": "value", "format": ".2%", "title": "Значение"}]}},
        {"transform": [{"filter": "datum.col == 'value_2026_10_01'"}],
         "mark": {"type": "text", "dy": -12, "fontSize": 11, "color": "#0b0b0b"},
         "encoding": {"text": {"field": "value", "format": ".1%"}}},
    ],
)
write("dynamics", dyn)

# 3. Структура по корзинам просрочки
dpd = spec(
    title="Структура портфеля по просрочке, млрд ₸",
    width=640, height=110,
    data={"url": "data/v_dpd_buckets.json"},
    transform=[DATE_LABEL, {"calculate": "datum.portfolio_kzt / 1e9", "as": "bn"},
               {"calculate": "indexof(['0','1-30','31-90','90+'], datum.dpd_bucket)", "as": "o"}],
    mark={"type": "bar", "stroke": "#fcfcfb", "strokeWidth": 2, "cornerRadiusEnd": 0},
    encoding={
        "y": {"field": "date_label", "type": "nominal", "title": None},
        "x": {"field": "bn", "type": "quantitative", "stack": "zero", "title": None},
        "order": {"field": "o"},
        "color": {"field": "dpd_bucket", "type": "ordinal", "title": "Просрочка, дней",
                  "scale": {"domain": ["0", "1-30", "31-90", "90+"],
                            "range": ["#cfe0f5", "#86b2e6", "#2a78d6", "#123e73"]},
                  "legend": {"orient": "top"}},
        "tooltip": [{"field": "date_label", "title": "Дата"}, {"field": "dpd_bucket", "title": "Просрочка"},
                    {"field": "bn", "format": ",.1f", "title": "млрд ₸"}],
    },
)
write("dpd_buckets", dpd)


# 4–5. Разрезы: отрасль и программа — объём портфеля и NPL на обе даты
def breakdown(dim, title):
    base = {"data": {"url": "data/v_breakdown.json"},
            "transform": [{"filter": f"datum.dim == '{dim}'"}, DATE_LABEL,
                          {"calculate": "datum.portfolio_kzt / 1e9", "as": "bn"}]}
    y = {"field": "dim_value", "type": "nominal", "title": None,
         "sort": {"op": "sum", "field": "portfolio_kzt", "order": "descending"}}
    tip = [{"field": "dim_value", "title": title}, {"field": "date_label", "title": "Дата"},
           {"field": "bn", "format": ",.1f", "title": "Портфель, млрд ₸"},
           {"field": "par30", "format": ".2%", "title": "PAR30"},
           {"field": "par90", "format": ".2%", "title": "PAR90"},
           {"field": "npl", "format": ".2%", "title": "NPL"}]
    return spec(
        title=f"Разрез по {'отрасли' if dim == 'sector' else 'программе'}",
        **base,
        hconcat=[
            {"title": {"text": "Портфель, млрд ₸", "fontSize": 12, "fontWeight": 400},
             "width": 280, "height": {"step": 13},
             "mark": {"type": "bar", "cornerRadiusEnd": 4},
             "encoding": {"y": y, "yOffset": {"field": "date_label", "sort": ["01.09.2026", "01.10.2026"]},
                          "x": {"field": "bn", "type": "quantitative", "title": None},
                          "color": DATE_COLOR, "tooltip": tip}},
            {"title": {"text": "NPL, доля портфеля сегмента", "fontSize": 12, "fontWeight": 400},
             "width": 280, "height": {"step": 26},
             "encoding": {"y": {**y, "axis": None},
                          "x": {"field": "npl", "type": "quantitative", "axis": {"format": ".0%"}, "title": None}},
             "layer": [
                 {"mark": {"type": "line", "color": "#c9c8c2", "strokeWidth": 2},
                  "encoding": {"detail": {"field": "dim_value"}}},
                 {"mark": {"type": "point", "filled": True, "size": 90, "opacity": 1},
                  "encoding": {"color": DATE_COLOR, "tooltip": tip}},
             ]},
        ],
    )


write("by_sector", breakdown("sector", "Отрасль"))
write("by_program", breakdown("program", "Программа"))

# 6. Топ-10 групп связанных заёмщиков на 01.10
top = spec(
    title="Топ-10 групп связанных заёмщиков на 01.10.2026, доля портфеля",
    width=640, height={"step": 20},
    data={"url": "data/v_top_groups.json"},
    transform=[{"filter": "datum.report_date == '2026-10-01'"}],
    encoding={"y": {"field": "group_id", "type": "nominal", "sort": {"field": "rnk"}, "title": None},
              "x": {"field": "share", "type": "quantitative", "axis": {"format": ".1%"}, "title": None}},
    layer=[
        {"mark": {"type": "bar", "color": "#2a78d6", "cornerRadiusEnd": 4},
         "encoding": {"tooltip": [{"field": "group_id", "title": "Группа"},
                                  {"field": "share", "format": ".2%", "title": "Доля"},
                                  {"field": "exposure_kzt", "format": ",.0f", "title": "Остаток, ₸"}]}},
        {"mark": {"type": "text", "align": "left", "dx": 4, "fontSize": 11, "color": "#52514e"},
         "encoding": {"text": {"field": "share", "format": ".1%"}}},
    ],
)
write("top_groups", top)
print("specs:", sorted(p.name for p in OUT.glob("*.json")))
