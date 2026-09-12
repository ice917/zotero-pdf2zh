# -*- coding: utf-8 -*-
"""
MinerU Markdown 翻译脚本(公式保护版)
=====================================
功能: 读取 MinerU 生成的 markdown,提取 $..$ / $$..$$ 公式块为占位符,
      只翻译正文文字(调用 SiliconFlow / 任意 OpenAI 兼容 API),
      翻译完成后把占位符还原为原样 LaTeX 公式。

用法:
    python translate_md.py <输入.md> <输出.md> [--config config.json]

依赖: 仅标准库(urllib),无第三方依赖。
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

# ==================== 默认配置(可用 --config 覆盖) ====================
DEFAULT_CONFIG = {
    "api_key": "",                    # SiliconFlow API Key (sk- 开头)
    "base_url": "https://api.siliconflow.cn/v1/chat/completions",
    "model": "Qwen/Qwen2.5-7B-Instruct",
    "source_lang": "English",
    "target_lang": "Simplified Chinese",
    "chunk_size": 4000,               # 每个请求最大字符数
    "max_workers": 5,                 # 并发请求数(提速关键)
    "max_retries": 3,                 # 单块失败重试次数
    "request_timeout": 180,           # 单请求超时(秒)
    "temperature": 0.3,
}
# ======================================================================

# 公式占位符: 形如 【公式1】【公式2】...
_PH_PREFIX = "\u3010\u516c\u5f0f"      # 【公式
_PH_SUFFIX = "\u3011"                   # 】
_PH_RE = re.compile(re.escape(_PH_PREFIX) + r"(\d+)" + re.escape(_PH_SUFFIX))

# 公式块正则: $$..$$ 优先,再匹配单 $..$ (不匹配 \$ 转义和空 $)
_FORMULA_RE = re.compile(r"\$\$[\s\S]*?\$\$|\$(?!\$)[^\$\n]*?\$")


def protect_formulas(text):
    """把公式替换为占位符,返回 (替换后文本, {占位符: 公式原文})"""
    formulas = {}

    def _repl(m):
        idx = len(formulas) + 1
        ph = f"{_PH_PREFIX}{idx}{_PH_SUFFIX}"
        formulas[ph] = m.group(0)
        return ph

    return _FORMULA_RE.sub(_repl, text), formulas


def restore_formulas(text, formulas):
    """把占位符还原为公式原文"""
    for ph, formula in formulas.items():
        text = text.replace(ph, formula)
    return text


def split_chunks(text, chunk_size):
    """按行切块,尽量在空行处断开,单块不超过 chunk_size 字符"""
    lines = text.split("\n")
    chunks = []
    cur = []
    cur_len = 0
    for line in lines:
        if cur_len + len(line) + 1 > chunk_size and cur:
            chunks.append("\n".join(cur))
            cur = []
            cur_len = 0
        cur.append(line)
        cur_len += len(line) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def call_llm(text, cfg):
    """调用 OpenAI 兼容 API 翻译一块文本"""
    system_prompt = (
        f"You are a professional academic translator. "
        f"Translate the following {cfg['source_lang']} text into {cfg['target_lang']}.\n"
        f"RULES:\n"
        f"1. Keep all Markdown structure exactly (headings #, lists -, tables |, code fences ```).\n"
        f"2. Keep ALL placeholders like {_PH_PREFIX}N{_PH_SUFFIX} verbatim in the same positions.\n"
        f"3. Keep numbers, units (mm, acres, etc.), and symbols unchanged.\n"
        f"4. Output ONLY the translated text, no explanations."
    )
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        "temperature": cfg.get("temperature", 0.3),
    }
    req = urllib.request.Request(
        cfg["base_url"],
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg['api_key']}",
        },
    )
    with urllib.request.urlopen(req, timeout=cfg.get("request_timeout", 180)) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def translate_text(text, cfg):
    """整段文本翻译,带重试"""
    last_err = None
    for attempt in range(1, cfg["max_retries"] + 1):
        try:
            return call_llm(text, cfg)
        except Exception as e:
            last_err = e
            print(f"  [重试 {attempt}/{cfg['max_retries']}] 翻译失败: {e}", file=sys.stderr)
            time.sleep(3 * attempt)
    raise RuntimeError(f"翻译失败(重试 {cfg['max_retries']} 次仍失败): {last_err}")


def translate_markdown(src_path, dst_path, cfg):
    print(f"[1/4] 读取 {src_path}")
    with open(src_path, "r", encoding="utf-8") as f:
        raw = f.read()

    print("[2/4] 保护公式块")
    protected, formulas = protect_formulas(raw)
    print(f"      共提取公式 {len(formulas)} 个")

    chunks = split_chunks(protected, cfg["chunk_size"])
    print(f"[3/4] 分 {len(chunks)} 块翻译(并发 {cfg.get('max_workers', 5)} 路)")

    # 过滤无需翻译的块(空块 / 纯公式占位符块)
    tasks = []  # (index, chunk)
    for i, chunk in enumerate(chunks):
        stripped = chunk.strip()
        if not stripped:
            tasks.append((i, chunk, None))
            continue
        t = stripped.replace(" ", "")
        if re.fullmatch(f"({_PH_PREFIX}\\d+{_PH_SUFFIX}|[\\s\\n])+", t):
            tasks.append((i, chunk, None))
            continue
        tasks.append((i, chunk, "translate"))

    results = {}
    workers = cfg.get("max_workers", 5)
    done_count = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for i, chunk, action in tasks:
            if action == "translate":
                fut = pool.submit(translate_text, chunk, cfg)
                futures[fut] = i
            else:
                results[i] = chunk
        for fut in as_completed(futures):
            i = futures[fut]
            try:
                results[i] = fut.result().strip()
            except Exception as e:
                print(f"      块 {i + 1} 最终失败: {e}", file=sys.stderr)
                results[i] = None
            done_count += 1
            print(f"      进度: {done_count}/{len(futures)} 块完成")

    # 按原始顺序组装(失败的块保留原文)
    ordered = []
    for i, chunk, action in tasks:
        if action == "translate":
            out = results.get(i)
            ordered.append(out if out is not None else chunk)
        else:
            ordered.append(chunk)

    result = "\n".join(ordered)
    print("[4/4] 还原公式")
    result = restore_formulas(result, formulas)

    os.makedirs(os.path.dirname(dst_path) or ".", exist_ok=True)
    with open(dst_path, "w", encoding="utf-8") as f:
        f.write(result)
    print(f"完成!输出: {dst_path}")


def main():
    parser = argparse.ArgumentParser(description="MinerU Markdown 翻译(公式保护)")
    parser.add_argument("src", help="输入 markdown 文件")
    parser.add_argument("dst", help="输出 markdown 文件")
    parser.add_argument("--config", help="JSON 配置文件(可选)")
    args = parser.parse_args()

    cfg = dict(DEFAULT_CONFIG)
    if args.config and os.path.exists(args.config):
        with open(args.config, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))

    if not cfg["api_key"]:
        print("错误: 未设置 api_key。", file=sys.stderr)
        print("方式1: 编辑脚本顶部 DEFAULT_CONFIG", file=sys.stderr)
        print("方式2: python translate_md.py in.md out.md --config myconfig.json", file=sys.stderr)
        sys.exit(1)

    translate_markdown(args.src, args.dst, cfg)


if __name__ == "__main__":
    main()
