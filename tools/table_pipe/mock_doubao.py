# -*- coding: utf-8 -*-
"""模拟豆包回包  v1 —— 仅用于演练对账机制, 不代表真实翻译质量。

读 job_doubao.txt 的待译行, 词典直替产中译(词典没有的词保留英文, 模拟真实 MT 的不完美);
占位符 {S###} 原样抄写。
  --violate  在正常回包基础上注入 4 类违规, 验证门禁有牙齿:
             丢一行 / 重复一行 / 占位符改写位数+删除一个 / 占位符外裸数字

用法: python mock_doubao.py [--violate]
"""
import io
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SD = os.path.dirname(os.path.abspath(__file__))          # 脚本目录(本件所在)
D = os.environ.get("P2Z_TABLE_DIR") or SD                # 工作目录(数据所在), 缺省=脚本目录

DICT = {
    # 表头
    "Subfamily": "亚科", "Genera (n)": "属数 (n)", "Species (n)": "种数 (n)",
    "Life form": "生活型", "Geographic distribution": "地理分布", "Metabolism": "代谢类型",
    "Shape": "花形状", "Individual sexual unit": "个体性单元", "Symmetry": "对称性",
    "Color of perianth segments": "花被片颜色", "Corolla aperture (cm)": "花冠口径 (cm)",
    "Inﬂorescence": "花序", "Inbreeding depression": "近交衰退",
    "Species": "物种", "Subfamily-tribe": "亚科-族",
    "Floral longevity (days)": "花寿命 (天)", "Compatibility": "亲和性",
    "Mating system morphological-functional": "形态-功能交配系统",
    "Outcrossing rate estimation method": "异交率估计方法",
    "Nectar": "花蜜", "Pollination syndrome": "传粉综合征", "Reference": "文献",
    # T1 数据
    "Treelike or shrub-like with long-lasting leaves": "乔木状或灌木状, 具长命叶",
    "Shrub-like as short cushions": "灌木状, 呈短垫状",
    "Treelike, shrub-like cylindrical, expansive": "乔木状、灌木状、圆柱状、扩展状",
    "Treelike, shrub-like": "乔木状, 灌木状",
    "Lowland neotropics from southern Mexico, Caribbean region, Central America, and to northern Argentina":
        "低地新热带: 墨西哥南部、加勒比地区、中美洲, 南至阿根廷北部",
    "Chile, Southern Andes throughout Patagonia, Argentina": "智利, 南安第斯山脉至巴塔哥尼亚, 阿根廷",
    "From Canada throughout South America and Caribbean": "从加拿大遍布南美洲和加勒比地区",
    "Floral cup, SHFT": "花杯, SHFT", "Floral cup, SHFT, LFT": "花杯, SHFT, LFT",
    "Red, pink to white": "红、粉至白", "Yellow, white": "黄、白",
    "Yellow, pink, red, green off-white": "黄、粉、红、绿、乳白",
    "Yellow, pink, magenta-blue, purple, orange, red, white": "黄、粉、品红-蓝、紫、橙、红、白",
    "Clusters or solitaries": "簇生或单生", "Solitary": "单生",
    # T2 数据
    "Fruit set": "坐果", "Fruits": "果实", "seeds": "种子", "Seeds": "种子",
    "germination": "萌发", "seedlings": "幼苗", "No": "无",
    "Fruit set, seeds/fruit": "坐果, 种子/果实", "Fruits, seeds": "果实, 种子",
    "Fruits, seeds, germination": "果实, 种子, 萌发",
    "Fruit set, germination, seedlings, seeds/fruit": "坐果, 萌发, 幼苗, 种子/果实",
    "Melittophily": "蜂媒", "Chiropterophily": "蝠媒", "Ornithophily": "鸟媒",
    "Phalaenophily": "蛾媒", "Mirmecophily": "蚁媒",
    "Two pollinators": "两种传粉者", "Three pollinators": "三种传粉者",
}


def tr(t):
    for k in sorted(DICT, key=len, reverse=True):
        t = re.sub(r"(?<![A-Za-z])%s(?![A-Za-z])" % re.escape(k), DICT[k], t)
    return t


def main():
    violate = "--violate" in sys.argv
    lines = []
    with io.open(os.path.join(D, "job_doubao.txt"), encoding="utf-8") as f:
        for ln in f:
            m = re.match(r"^(k\d{3})\t(.*)$", ln.rstrip("\n"))
            if m:
                lines.append("%s\t%s" % (m.group(1), tr(m.group(2))))

    out = os.path.join(D, "job_response_bad.tsv" if violate else "job_response.tsv")
    if violate:
        del lines[6]                                    # 丢一行
        lines.insert(9, lines[9])                       # 重复一行
        for i, ln in enumerate(lines):                  # 占位符改写 + 删一个
            phs = re.findall(r"\{S\d{3}\}", ln)
            if len(phs) >= 2:
                lines[i] = ln.replace(phs[0], "{S%d}" % int(phs[0][2:5]), 1) \
                             .replace(phs[1], "", 1)
                break
        lines[2] += " (see 3.14)"                       # 裸数字
        out = os.path.join(D, "job_response_bad.tsv")
    with io.open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("%s: %d 行%s" % (out, len(lines), " (已注入违规)" if violate else ""))


main()
