import csv
import html
import json
import logging
import os
import re
import threading
import time
import unicodedata
from collections import deque
from copy import copy
from string import Template
from typing import cast
import deepl
import ollama
import openai
import requests
import xinference_client
from azure.ai.translation.text import TextTranslationClient
from azure.core.credentials import AzureKeyCredential
from tencentcloud.common import credential
from tencentcloud.tmt.v20180321.models import (
    TextTranslateRequest,
    TextTranslateResponse,
)
from tencentcloud.tmt.v20180321.tmt_client import TmtClient

from pdf2zh.cache import TranslationCache
from pdf2zh.config import ConfigManager


from tenacity import retry, retry_if_exception_type
from tenacity import stop_after_attempt
from tenacity import wait_exponential


logger = logging.getLogger(__name__)


def remove_control_characters(s):
    return "".join(ch for ch in s if unicodedata.category(ch)[0] != "C")


class BaseTranslator:
    name = "base"
    envs = {}
    lang_map: dict[str, str] = {}
    CustomPrompt = False

    def __init__(self, lang_in: str, lang_out: str, model: str, ignore_cache: bool):
        lang_in = self.lang_map.get(lang_in.lower(), lang_in)
        lang_out = self.lang_map.get(lang_out.lower(), lang_out)
        self.lang_in = lang_in
        self.lang_out = lang_out
        self.model = model
        self.ignore_cache = ignore_cache

        self.cache = TranslationCache(
            self.name,
            {
                "lang_in": lang_in,
                "lang_out": lang_out,
                "model": model,
            },
        )

    def set_envs(self, envs):
        # Detach from self.__class__.envs
        # Cannot use self.envs = copy(self.__class__.envs)
        # because if set_envs called twice, the second call will override the first call
        self.envs = copy(self.envs)
        manager_envs = ConfigManager.get_translator_by_name(self.name)
        if manager_envs:
            for key, value in manager_envs.items():
                if value not in (None, "", [], {}):
                    self.envs[key] = value
        needUpdate = False
        for key in self.envs:
            if key in os.environ:
                self.envs[key] = os.environ[key]
                needUpdate = True
        if envs is not None:
            for key in envs:
                value = envs[key]
                if value not in (None, "", [], {}):
                    self.envs[key] = value
        ConfigManager.set_translator_by_name(self.name, self.envs)

    def add_cache_impact_parameters(self, k: str, v):
        """
        Add parameters that affect the translation quality to distinguish the translation effects under different parameters.
        :param k: key
        :param v: value
        """
        self.cache.add_params(k, v)

    def translate(self, text: str, ignore_cache: bool = False) -> str:
        """
        Translate the text, and the other part should call this method.
        :param text: text to translate
        :return: translated text
        """
        if not (self.ignore_cache or ignore_cache):
            cache = self.cache.get(text)
            if cache is not None:
                return cache

        translation = self.do_translate(text)
        # 润色钩子: 若 translator 定义了 polish 方法, 对初译结果做二次润色
        polish = getattr(self, "polish", None)
        if polish is not None:
            try:
                translation = polish(text, translation)
            except Exception:
                logger.exception("Polish hook failed, fall back to raw translation.")
        self.cache.set(text, translation)
        return translation

    def do_translate(self, text: str) -> str:
        """
        Actual translate text, override this method
        :param text: text to translate
        :return: translated text
        """
        raise NotImplementedError

    def prompt(
        self, text: str, prompt_template: Template | None = None
    ) -> list[dict[str, str]]:
        try:
            return [
                {
                    "role": "user",
                    "content": cast(Template, prompt_template).safe_substitute(
                        {
                            "lang_in": self.lang_in,
                            "lang_out": self.lang_out,
                            "text": text,
                        }
                    ),
                }
            ]
        except AttributeError:  # `prompt_template` is None
            pass
        except Exception:
            logging.exception("Error parsing prompt, use the default prompt.")

        return [
            {
                "role": "user",
                "content": (
                    "You are a professional, authentic machine translation engine. "
                    "Only Output the translated text, do not include any other text."
                    "\n\n"
                    f"Translate the following markdown source text to {self.lang_out}. "
                    "Keep the formula notation {v*} unchanged. "
                    "Output translation directly without any additional text."
                    "\n\n"
                    f"Source Text: {text}"
                    "\n\n"
                    "Translated Text:"
                ),
            },
        ]

    def __str__(self):
        return f"{self.name} {self.lang_in} {self.lang_out} {self.model}"

    def get_rich_text_left_placeholder(self, id: int):
        return f"<b{id}>"

    def get_rich_text_right_placeholder(self, id: int):
        return f"</b{id}>"

    def get_formular_placeholder(self, id: int):
        return self.get_rich_text_left_placeholder(
            id
        ) + self.get_rich_text_right_placeholder(id)


class GoogleTranslator(BaseTranslator):
    name = "google"
    lang_map = {"zh": "zh-CN"}

    def __init__(self, lang_in, lang_out, model, ignore_cache=False, **kwargs):
        super().__init__(lang_in, lang_out, model, ignore_cache)
        self.session = requests.Session()
        self.endpoint = "https://translate.google.com/m"
        self.headers = {
            "User-Agent": "Mozilla/4.0 (compatible;MSIE 6.0;Windows NT 5.1;SV1;.NET CLR 1.1.4322;.NET CLR 2.0.50727;.NET CLR 3.0.04506.30)"  # noqa: E501
        }

    def do_translate(self, text):
        text = text[:5000]  # google translate max length
        response = self.session.get(
            self.endpoint,
            params={"tl": self.lang_out, "sl": self.lang_in, "q": text},
            headers=self.headers,
        )
        re_result = re.findall(
            r'(?s)class="(?:t0|result-container)">(.*?)<', response.text
        )
        if response.status_code == 400:
            result = "IRREPARABLE TRANSLATION ERROR"
        else:
            response.raise_for_status()
            result = html.unescape(re_result[0])
        return remove_control_characters(result)


class BingTranslator(BaseTranslator):
    # https://github.com/immersive-translate/old-immersive-translate/blob/6df13da22664bea2f51efe5db64c63aca59c4e79/src/background/translationService.js
    name = "bing"
    lang_map = {"zh": "zh-Hans"}

    def __init__(self, lang_in, lang_out, model, ignore_cache=False, **kwargs):
        super().__init__(lang_in, lang_out, model, ignore_cache)
        self.session = requests.Session()
        self.endpoint = "https://www.bing.com/translator"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",  # noqa: E501
        }

    def find_sid(self):
        response = self.session.get(self.endpoint)
        response.raise_for_status()
        url = response.url[:-10]
        ig = re.findall(r"\"ig\":\"(.*?)\"", response.text)[0]
        iid = re.findall(r"data-iid=\"(.*?)\"", response.text)[-1]
        key, token = re.findall(
            r"params_AbusePreventionHelper\s=\s\[(.*?),\"(.*?)\",", response.text
        )[0]
        return url, ig, iid, key, token

    def do_translate(self, text):
        text = text[:1000]  # bing translate max length
        url, ig, iid, key, token = self.find_sid()
        response = self.session.post(
            f"{url}ttranslatev3?IG={ig}&IID={iid}",
            data={
                "fromLang": self.lang_in,
                "to": self.lang_out,
                "text": text,
                "token": token,
                "key": key,
            },
            headers=self.headers,
        )
        response.raise_for_status()
        return response.json()[0]["translations"][0]["text"]


class DeepLTranslator(BaseTranslator):
    # https://github.com/DeepLcom/deepl-python
    name = "deepl"
    envs = {
        "DEEPL_AUTH_KEY": None,
    }
    lang_map = {"zh": "zh-Hans"}

    def __init__(
        self, lang_in, lang_out, model, envs=None, ignore_cache=False, **kwargs
    ):
        self.set_envs(envs)
        super().__init__(lang_in, lang_out, model, ignore_cache)
        auth_key = self.envs["DEEPL_AUTH_KEY"]
        self.client = deepl.Translator(auth_key)

    def do_translate(self, text):
        response = self.client.translate_text(
            text, target_lang=self.lang_out, source_lang=self.lang_in
        )
        return response.text


class DeepLXTranslator(BaseTranslator):
    # https://deeplx.owo.network/endpoints/free.html
    name = "deeplx"
    envs = {
        "DEEPLX_ENDPOINT": "https://api.deepl.com/translate",
        "DEEPLX_ACCESS_TOKEN": None,
    }
    lang_map = {"zh": "zh-Hans"}

    def __init__(
        self, lang_in, lang_out, model, envs=None, ignore_cache=False, **kwargs
    ):
        self.set_envs(envs)
        super().__init__(lang_in, lang_out, model, ignore_cache)
        self.endpoint = self.envs["DEEPLX_ENDPOINT"]
        self.session = requests.Session()
        auth_key = self.envs["DEEPLX_ACCESS_TOKEN"]
        if auth_key:
            self.endpoint = f"{self.endpoint}?token={auth_key}"

    def do_translate(self, text):
        response = self.session.post(
            self.endpoint,
            json={
                "source_lang": self.lang_in,
                "target_lang": self.lang_out,
                "text": text,
            },
            verify=False,  # noqa: S506
        )
        response.raise_for_status()
        return response.json()["data"]


class OllamaTranslator(BaseTranslator):
    # https://github.com/ollama/ollama-python
    name = "ollama"
    envs = {
        "OLLAMA_HOST": "http://127.0.0.1:11434",
        "OLLAMA_MODEL": "gemma2",
    }
    CustomPrompt = True

    def __init__(
        self,
        lang_in: str,
        lang_out: str,
        model: str,
        envs=None,
        prompt: Template | None = None,
        ignore_cache=False,
    ):
        self.set_envs(envs)
        if not model:
            model = self.envs["OLLAMA_MODEL"]
        super().__init__(lang_in, lang_out, model, ignore_cache)
        self.options = {
            "temperature": 0,  # 随机采样可能会打断公式标记
            "num_predict": 2000,
        }
        self.client = ollama.Client(host=self.envs["OLLAMA_HOST"])
        self.prompt_template = prompt
        self.add_cache_impact_parameters("temperature", self.options["temperature"])

    def do_translate(self, text: str) -> str:
        if (max_token := len(text) * 5) > self.options["num_predict"]:
            self.options["num_predict"] = max_token

        response = self.client.chat(
            model=self.model,
            messages=self.prompt(text, self.prompt_template),
            options=self.options,
        )
        content = self._remove_cot_content(response.message.content or "")
        return content.strip()

    @staticmethod
    def _remove_cot_content(content: str) -> str:
        """Remove text content with the thought chain from the chat response

        :param content: Non-streaming text content
        :return: Text without a thought chain
        """
        return re.sub(r"^<think>.+?</think>", "", content, count=1, flags=re.DOTALL)


class XinferenceTranslator(BaseTranslator):
    # https://github.com/xorbitsai/inference
    name = "xinference"
    envs = {
        "XINFERENCE_HOST": "http://127.0.0.1:9997",
        "XINFERENCE_MODEL": "gemma-2-it",
    }
    CustomPrompt = True

    def __init__(
        self, lang_in, lang_out, model, envs=None, prompt=None, ignore_cache=False
    ):
        self.set_envs(envs)
        if not model:
            model = self.envs["XINFERENCE_MODEL"]
        super().__init__(lang_in, lang_out, model, ignore_cache)
        self.options = {"temperature": 0}  # 随机采样可能会打断公式标记
        self.client = xinference_client.RESTfulClient(self.envs["XINFERENCE_HOST"])
        self.prompttext = prompt
        self.add_cache_impact_parameters("temperature", self.options["temperature"])

    def do_translate(self, text):
        maxlen = max(2000, len(text) * 5)
        for model in self.model.split(";"):
            try:
                xf_model = self.client.get_model(model)
                xf_prompt = self.prompt(text, self.prompttext)
                xf_prompt = [
                    {
                        "role": "user",
                        "content": xf_prompt[0]["content"]
                        + "\n"
                        + xf_prompt[1]["content"],
                    }
                ]
                response = xf_model.chat(
                    generate_config=self.options,
                    messages=xf_prompt,
                )

                response = response["choices"][0]["message"]["content"].replace(
                    "<end_of_turn>", ""
                )
                if len(response) > maxlen:
                    raise Exception("Response too long")
                return response.strip()
            except Exception as e:
                print(e)
        raise Exception("All models failed")


class OpenAITranslator(BaseTranslator):
    # https://github.com/openai/openai-python
    name = "openai"
    envs = {
        "OPENAI_BASE_URL": "https://api.openai.com/v1",
        "OPENAI_API_KEY": None,
        "OPENAI_MODEL": "gpt-4o-mini",
    }
    CustomPrompt = True

    def __init__(
        self,
        lang_in,
        lang_out,
        model,
        base_url=None,
        api_key=None,
        envs=None,
        prompt=None,
        ignore_cache=False,
    ):
        self.set_envs(envs)
        if not model:
            model = self.envs["OPENAI_MODEL"]
        super().__init__(lang_in, lang_out, model, ignore_cache)
        self.options = {"temperature": 0}  # 随机采样可能会打断公式标记
        self.client = openai.OpenAI(
            base_url=base_url or self.envs["OPENAI_BASE_URL"],
            api_key=api_key or self.envs["OPENAI_API_KEY"],
        )
        self.prompttext = prompt
        self.add_cache_impact_parameters("temperature", self.options["temperature"])
        self.add_cache_impact_parameters("prompt", self.prompt("", self.prompttext))
        think_filter_regex = r"^<think>.+?\n*(</think>|\n)*(</think>)\n*"
        self.add_cache_impact_parameters("think_filter_regex", think_filter_regex)
        self.think_filter_regex = re.compile(think_filter_regex, flags=re.DOTALL)
        # 润色钩子配置: POLISH=1 开启, POLISH_GLOSSARY 为可选术语表 CSV, POLISH_MODEL 可选指定润色模型
        self._polish_enabled = str(self.envs.get("POLISH", "")).lower() in ("1", "true", "on")
        self._polish_glossary_path = self.envs.get("POLISH_GLOSSARY") or None
        self._polish_glossary = None
        # 上下文窗口按线程隔离: 并发翻译时段落顺序互不污染
        self._tls = threading.local()
        # 反思模式: 先审校批判, 再按建议修订 (吴恩达 translation-agent 三步流)
        self._polish_reflect = str(self.envs.get("POLISH_REFLECT", "")).lower() in ("1", "true", "on")
        # 确定性锚点断言: 数字保真+不译词保留, 不经 LLM 无盲区 (默认开, POLISH_ANCHOR=0 关闭)
        self._polish_anchor = str(self.envs.get("POLISH_ANCHOR", "1")).lower() in ("1", "true", "on")
        # 翻译指南: 每篇论文一份的语域/术语/风格约定, 注入每次润色
        self._polish_guideline_path = self.envs.get("POLISH_GUIDELINE") or None
        self._polish_guideline_text = None
        # 人审报告: 审校批判结果落盘为 Markdown, 供人工终审 (目录由 POLISH_REPORT_DIR 指定)
        self._polish_report_dir = self.envs.get("POLISH_REPORT_DIR") or None
        self._polish_report_file = None
        self._polish_report_seq = 0
        if self._polish_report_dir and self._polish_enabled:
            ts_pid = f"{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"
            self._polish_report_file = os.path.join(
                self._polish_report_dir, f"审校报告_{ts_pid}.md")
            self._polish_doubt_file = os.path.join(
                self._polish_report_dir, f"存疑清单_{ts_pid}.md")
        # 启动自检: 钩子是否存活/配置是否生效, 在服务器控制台可见 (防静默失效)
        if self._polish_enabled:
            self.add_cache_impact_parameters("polish", "on")
            if self._polish_anchor:
                self.add_cache_impact_parameters("polish_anchor", "on")
            print(
                "[润色钩子] 已加载 | "
                f"反思={'开' if self._polish_reflect else '关'} | "
                f"锚点断言={'开' if self._polish_anchor else '关'} | "
                f"人审报告={'开' if self._polish_report_file else '关'} | "
                f"术语表={os.path.basename(self._polish_glossary_path) if self._polish_glossary_path else '无'} | "
                f"指南={os.path.basename(self._polish_guideline_path) if self._polish_guideline_path else '无'}",
                flush=True,
            )
        if self._polish_reflect:
            self.add_cache_impact_parameters("polish_reflect", "on")

    @property
    def _polish_context(self):
        """线程局部的上下文窗口 (并发翻译时每条线程独立维护自己的前文)."""
        if not hasattr(self._tls, "polish_ctx"):
            self._tls.polish_ctx = deque(maxlen=3)
        return self._tls.polish_ctx

    @retry(
        retry=retry_if_exception_type(openai.RateLimitError),
        stop=stop_after_attempt(100),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        before_sleep=lambda retry_state: logger.warning(
            f"RateLimitError, retrying in {retry_state.next_action.sleep} seconds... "
            f"(Attempt {retry_state.attempt_number}/100)"
        ),
    )
    def do_translate(self, text) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            **self.options,
            messages=self.prompt(text, self.prompttext),
        )
        if not response.choices:
            if hasattr(response, "error"):
                raise ValueError("Error response from Service", response.error)
        content = response.choices[0].message.content.strip()
        content = self.think_filter_regex.sub("", content).strip()
        return content

    def _load_polish_glossary(self) -> dict:
        path = self._polish_glossary_path
        if not path:
            return {}
        glossary = {}
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                for row in csv.reader(f):
                    if len(row) >= 2 and row[0].strip() and row[1].strip():
                        glossary[row[0].strip()] = row[1].strip()
        except Exception:
            logger.exception("Failed to load polish glossary, ignore it.")
        return glossary

    def _load_text_file(self, path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                return f.read().strip()
        except Exception:
            logger.exception("Failed to load polish config file: %s", path)
            return ""

    def _polish_llm(self, messages) -> str:
        response = self.client.chat.completions.create(
            model=self.envs.get("POLISH_MODEL") or self.model,
            **self.options,
            messages=messages,
        )
        content = response.choices[0].message.content.strip()
        return self.think_filter_regex.sub("", content).strip()

    def _polish_report_append(self, source: str, draft: str, critique: str, revised: str):
        """审校报告落盘: 原文/初译/建议/修订 四栏对照, 供人工终审."""
        if not self._polish_report_file:
            return
        try:
            os.makedirs(self._polish_report_dir, exist_ok=True)
            self._polish_report_seq += 1
            with open(self._polish_report_file, "a", encoding="utf-8") as f:
                f.write(f"## 段落 {self._polish_report_seq}\n\n")
                f.write(f"**原文**\n\n{source}\n\n")
                f.write(f"**初译**\n\n{draft}\n\n")
                f.write(f"**审校建议**\n\n{critique}\n\n")
                f.write(f"**自动修订**\n\n{revised}\n\n---\n\n")
        except Exception:
            logger.exception("Failed to write polish review report.")

    def _extract_doubt(self, critique: str) -> str:
        m = re.search(r"【存疑】(.*?)(?=\n【|$)", critique, flags=re.DOTALL)
        return m.group(1).strip() if m else ""

    def _polish_doubt_append(self, source: str, draft: str, doubt: str):
        """存疑清单单独落盘: 人审疲劳的解法, 只看这一份小文件即可裁决术语冲突."""
        if not self._polish_doubt_file or not doubt:
            return
        if doubt.strip() in ("无", "无明显问题", "无明显问题。"):
            return
        try:
            os.makedirs(self._polish_report_dir, exist_ok=True)
            with open(self._polish_doubt_file, "a", encoding="utf-8") as f:
                f.write(f"## 存疑段落 {self._polish_report_seq}\n\n")
                f.write(f"**原文**\n\n{source}\n\n**初译(保持未动)**\n\n{draft}\n\n")
                f.write(f"**存疑点**\n\n{doubt}\n\n---\n\n")
        except Exception:
            logger.exception("Failed to write doubt list.")

    def _anchor_check(self, source: str, content: str):
        """确定性锚点断言 (不经 LLM, 无盲区). 返回 (不译词丢失列表, 数字缺失列表).
        检查项: ①数字保真 ②不译词保留(术语表中 target==source 的条目)."""
        # 剔除占位符与文献引用括号(如 [15–17, 24]), 避免区间端点误报
        src_clean = re.sub(r"\{\{v\d+\}\}", " ", source)
        src_clean = re.sub(r"\[[^\]]*\]", " ", src_clean)
        out_clean = re.sub(r"\{\{v\d+\}\}", " ", content)
        # ① 数字保真: 原文数字必须全部出现在译文中 (千分位归一化)
        src_nums = set(
            n.replace(",", "")
            for n in re.findall(r"\d[\d,]*(?:\.\d+)?", src_clean)
        )
        missing = sorted(n for n in src_nums if n not in out_clean.replace(",", ""))
        # ② 不译词保留: 缩写/专名在润色后必须原样存在 (仅检查原文中出现的词)
        lost = []
        if self._polish_glossary:
            lost = [
                k for k, v in self._polish_glossary.items()
                if k.strip().lower() == v.strip().lower()
                and k.lower() in src_clean.lower()
                and k.lower() not in out_clean.lower()
            ]
        return lost, missing

    def _polish_anchor_note(self, source: str, note: str):
        """锚点断言违规写入主报告, 供人工核查."""
        if not self._polish_report_file:
            return
        try:
            os.makedirs(self._polish_report_dir, exist_ok=True)
            self._polish_report_seq += 1
            with open(self._polish_report_file, "a", encoding="utf-8") as f:
                f.write(f"## 段落 {self._polish_report_seq}（锚点断言）\n\n")
                f.write(f"**原文**\n\n{source}\n\n**违规**\n\n{note}\n\n---\n\n")
        except Exception:
            logger.exception("Failed to write anchor assertion note.")

    def _polish_blocks(self, source: str, target: str, terms: str, context: str) -> str:
        """公共 prompt 块: 术语表 + 前文 + 原文 + 初译."""
        blocks = ""
        if context:
            blocks += f"前文参考(注意译名与指代一致):\n{context}\n\n"
        if terms:
            blocks += f"必须遵守的术语表:\n{terms}\n\n"
        blocks += f"原文:\n{source}\n\n初译:\n{target}\n\n"
        return blocks

    def polish(self, source: str, target: str) -> str:
        """二次润色: 上下文窗口 + 术语表 + 翻译指南; POLISH_REFLECT 开启时走'审校批判-修订'两段式."""
        if not self._polish_enabled:
            return target
        if self._polish_glossary is None:
            self._polish_glossary = self._load_polish_glossary()
        if self._polish_guideline_text is None:
            self._polish_guideline_text = (
                self._load_text_file(self._polish_guideline_path)
                if self._polish_guideline_path else ""
            )
        glossary = self._polish_glossary
        terms = "\n".join(
            f"- {k} => {v}"
            for k, v in glossary.items()
            if k.lower() in source.lower()
        )
        context = "\n".join(
            f"[前文{i+1}] 原文: {s}\n[前文{i+1}] 译文: {t}"
            for i, (s, t) in enumerate(self._polish_context)
        )
        guideline_block = (
            f"翻译指南(必须遵守):\n{self._polish_guideline_text}\n\n"
            if self._polish_guideline_text else ""
        )
        base_block = self._polish_blocks(source, target, terms, context)

        if self._polish_reflect:
            # 阶段1: 审校批判 (忠实性优先, 流畅性其次)
            critique_sys = (
                "你是资深学术译审, 对机器初译进行严格审校。只输出修改建议清单, 分三组:\n"
                "【忠实性】漏译/加译/错译/数字单位错误/逻辑扭曲, 逐条给出位置和改法;\n"
                "【流畅性】术语不一致/指代不清/欧化句式/生硬表达, 逐条给出位置和改法;\n"
                "【存疑】术语表译名与上下文语境冲突、或译名疑似不当需要人工裁决的条目, "
                "逐条说明冲突点(这些将提交人工终审, 不自动修改)。\n"
                "不输出译文本身。若确实无任何问题, 只输出: 无明显问题"
            )
            critique = self._polish_llm([
                {"role": "system", "content": critique_sys},
                {"role": "user", "content": guideline_block + base_block + "审校建议:"},
            ])
            critique_txt = critique
            # 无问题则跳过修订, 节省一次调用
            if "无明显问题" in critique and len(critique) < 30:
                content = target
                critique_txt = ""
            else:
                # 阶段2: 按建议修订
                revise_sys = (
                    "你是资深学术译者, 根据审校建议对初译做最终修订。要求:\n"
                    "1. 只落实【忠实性】【流畅性】两组建议, 不改写意思, 不增删信息; "
                    "使用正式严谨的学术语言;\n"
                    "2.【存疑】条目保持初译现状不动, 留待人工终审裁决;\n"
                    "3. 严禁修改任何形如 {{v数字}} 的占位符(公式/富文本标记), 必须原样保留;\n"
                    "4. 术语必须与术语表一致, 与前文译文译法保持一致;\n"
                    "5. 只输出修订后的译文, 不要任何解释或前后缀。"
                )
                content = self._polish_llm([
                    {"role": "system", "content": revise_sys},
                    {"role": "user", "content": (
                        guideline_block
                        + f"审校建议(逐条落实):\n{critique}\n\n"
                        + base_block + "修订后的译文:"
                    )},
                ])
        else:
            critique_txt = ""
            sys_prompt = (
                "你是资深学术译者, 对机器初译结果做最终润色。要求:\n"
                "1. 只润色, 不改写意思, 不增删信息; 使用正式、严谨、流畅的学术语言。\n"
                "2. 严禁修改任何形如 {{v数字}} 的占位符(公式/富文本标记), 必须原样保留, 数量与编号都不能变。\n"
                "3. 专业术语译法必须与术语表一致; 与前文译文中已采用的译法保持前后一致。\n"
                "4. 只输出润色后的译文, 不要任何解释或前后缀。"
            )
            content = self._polish_llm([
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": guideline_block + base_block + "润色后的译文:"},
            ])

        # 锚点断言 (确定性, 不经 LLM):
        #   不译词丢失 = 强信号, 初译完好则回退初译;
        #   数字缺失   = 弱信号 (存在合法数字转换如 1630 hours→16时30分), 只记录不回退
        if self._polish_anchor:
            lost_words, missing_nums = self._anchor_check(source, content)
            if lost_words:
                note = f"不译词丢失: {', '.join(lost_words[:8])}"
                _, lost_in_target = self._anchor_check(source, target)
                if not lost_in_target:
                    logger.warning(f"Anchor check failed, fall back to draft: {note}")
                    note += "（润色引入，已回退初译）"
                    content = target
                else:
                    note += "（初译同样存在，请人工核查）"
                self._polish_anchor_note(source, note)
            if missing_nums:
                self._polish_anchor_note(
                    source,
                    f"数字缺失(仅供参考, 可能是合法数字转换): {', '.join(missing_nums[:8])}")

        # 占位符保护: 润色若破坏 {{vN}} 占位符, 回退初译
        src_ph = set(re.findall(r"\{\{v\d+\}\}", target))
        dst_ph = set(re.findall(r"\{\{v\d+\}\}", content))
        if src_ph != dst_ph:
            logger.warning("Polish broke placeholders, fall back to raw translation.")
            content = target
        self._polish_context.append((source, content))

        # 审校报告与存疑清单落盘 (仅反思模式且批判有效时)
        if critique_txt:
            self._polish_report_append(source, target, critique_txt, content)
            self._polish_doubt_append(source, target, self._extract_doubt(critique_txt))
        return content

    def get_formular_placeholder(self, id: int):
        return "{{v" + str(id) + "}}"

    def get_rich_text_left_placeholder(self, id: int):
        return self.get_formular_placeholder(id)

    def get_rich_text_right_placeholder(self, id: int):
        return self.get_formular_placeholder(id + 1)


class AzureOpenAITranslator(BaseTranslator):
    name = "azure-openai"
    envs = {
        "AZURE_OPENAI_BASE_URL": None,  # e.g. "https://xxx.openai.azure.com"
        "AZURE_OPENAI_API_KEY": None,
        "AZURE_OPENAI_MODEL": "gpt-4o-mini",
        "AZURE_OPENAI_API_VERSION": "2024-06-01",  # default api version
    }
    CustomPrompt = True

    def __init__(
        self,
        lang_in,
        lang_out,
        model,
        base_url=None,
        api_key=None,
        envs=None,
        prompt=None,
        ignore_cache=False,
    ):
        self.set_envs(envs)
        base_url = self.envs["AZURE_OPENAI_BASE_URL"]
        if not model:
            model = self.envs["AZURE_OPENAI_MODEL"]
        api_version = self.envs.get("AZURE_OPENAI_API_VERSION", "2024-06-01")
        if api_key is None:
            api_key = self.envs["AZURE_OPENAI_API_KEY"]
        super().__init__(lang_in, lang_out, model, ignore_cache)
        self.options = {"temperature": 0}
        self.client = openai.AzureOpenAI(
            azure_endpoint=base_url,
            azure_deployment=model,
            api_version=api_version,
            api_key=api_key,
        )
        self.prompttext = prompt
        self.add_cache_impact_parameters("temperature", self.options["temperature"])
        self.add_cache_impact_parameters("prompt", self.prompt("", self.prompttext))

    def do_translate(self, text) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            **self.options,
            messages=self.prompt(text, self.prompttext),
        )
        return response.choices[0].message.content.strip()


class ModelScopeTranslator(OpenAITranslator):
    name = "modelscope"
    envs = {
        "MODELSCOPE_BASE_URL": "https://api-inference.modelscope.cn/v1",
        "MODELSCOPE_API_KEY": None,
        "MODELSCOPE_MODEL": "Qwen/Qwen2.5-32B-Instruct",
    }
    CustomPrompt = True

    def __init__(
        self,
        lang_in,
        lang_out,
        model,
        base_url=None,
        api_key=None,
        envs=None,
        prompt=None,
        ignore_cache=False,
    ):
        self.set_envs(envs)
        base_url = "https://api-inference.modelscope.cn/v1"
        api_key = self.envs["MODELSCOPE_API_KEY"]
        if not model:
            model = self.envs["MODELSCOPE_MODEL"]
        super().__init__(
            lang_in,
            lang_out,
            model,
            base_url=base_url,
            api_key=api_key,
            ignore_cache=ignore_cache,
        )
        self.prompttext = prompt
        self.add_cache_impact_parameters("prompt", self.prompt("", self.prompttext))


class ZhipuTranslator(OpenAITranslator):
    # https://bigmodel.cn/dev/api/thirdparty-frame/openai-sdk
    name = "zhipu"
    envs = {
        "ZHIPU_API_KEY": None,
        "ZHIPU_MODEL": "glm-4-flash",
    }
    CustomPrompt = True

    def __init__(
        self, lang_in, lang_out, model, envs=None, prompt=None, ignore_cache=False
    ):
        self.set_envs(envs)
        base_url = "https://open.bigmodel.cn/api/paas/v4"
        api_key = self.envs["ZHIPU_API_KEY"]
        if not model:
            model = self.envs["ZHIPU_MODEL"]
        super().__init__(
            lang_in,
            lang_out,
            model,
            base_url=base_url,
            api_key=api_key,
            ignore_cache=ignore_cache,
        )
        self.prompttext = prompt
        self.add_cache_impact_parameters("prompt", self.prompt("", self.prompttext))

    def do_translate(self, text) -> str:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                **self.options,
                messages=self.prompt(text, self.prompttext),
            )
        except openai.BadRequestError as e:
            if (
                json.loads(response.choices[0].message.content.strip())["error"]["code"]
                == "1301"
            ):
                return "IRREPARABLE TRANSLATION ERROR"
            raise e
        return response.choices[0].message.content.strip()


class SiliconTranslator(OpenAITranslator):
    # https://docs.siliconflow.cn/quickstart
    name = "silicon"
    envs = {
        "SILICON_API_KEY": None,
        "SILICON_MODEL": "Qwen/Qwen2.5-7B-Instruct",
    }
    CustomPrompt = True

    def __init__(
        self, lang_in, lang_out, model, envs=None, prompt=None, ignore_cache=False
    ):
        self.set_envs(envs)
        base_url = "https://api.siliconflow.cn/v1"
        api_key = self.envs["SILICON_API_KEY"]
        if not model:
            model = self.envs["SILICON_MODEL"]
        super().__init__(
            lang_in,
            lang_out,
            model,
            base_url=base_url,
            api_key=api_key,
            ignore_cache=ignore_cache,
        )
        self.prompttext = prompt
        self.add_cache_impact_parameters("prompt", self.prompt("", self.prompttext))


class GeminiTranslator(OpenAITranslator):
    # https://ai.google.dev/gemini-api/docs/openai
    name = "gemini"
    envs = {
        "GEMINI_API_KEY": None,
        "GEMINI_MODEL": "gemini-1.5-flash",
    }
    CustomPrompt = True

    def __init__(
        self, lang_in, lang_out, model, envs=None, prompt=None, ignore_cache=False
    ):
        self.set_envs(envs)
        base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
        api_key = self.envs["GEMINI_API_KEY"]
        if not model:
            model = self.envs["GEMINI_MODEL"]
        super().__init__(
            lang_in,
            lang_out,
            model,
            base_url=base_url,
            api_key=api_key,
            ignore_cache=ignore_cache,
        )
        self.prompttext = prompt
        self.add_cache_impact_parameters("prompt", self.prompt("", self.prompttext))


class AzureTranslator(BaseTranslator):
    # https://github.com/Azure/azure-sdk-for-python
    name = "azure"
    envs = {
        "AZURE_ENDPOINT": "https://api.translator.azure.cn",
        "AZURE_API_KEY": None,
    }
    lang_map = {"zh": "zh-Hans"}

    def __init__(
        self, lang_in, lang_out, model, envs=None, ignore_cache=False, **kwargs
    ):
        self.set_envs(envs)
        super().__init__(lang_in, lang_out, model, ignore_cache)
        endpoint = self.envs["AZURE_ENDPOINT"]
        api_key = self.envs["AZURE_API_KEY"]
        credential = AzureKeyCredential(api_key)
        self.client = TextTranslationClient(
            endpoint=endpoint, credential=credential, region="chinaeast2"
        )
        # https://github.com/Azure/azure-sdk-for-python/issues/9422
        logger = logging.getLogger("azure.core.pipeline.policies.http_logging_policy")
        logger.setLevel(logging.WARNING)

    def do_translate(self, text) -> str:
        response = self.client.translate(
            body=[text],
            from_language=self.lang_in,
            to_language=[self.lang_out],
        )
        translated_text = response[0].translations[0].text
        return translated_text


class TencentTranslator(BaseTranslator):
    # https://github.com/TencentCloud/tencentcloud-sdk-python
    name = "tencent"
    envs = {
        "TENCENTCLOUD_SECRET_ID": None,
        "TENCENTCLOUD_SECRET_KEY": None,
    }

    def __init__(
        self, lang_in, lang_out, model, envs=None, ignore_cache=False, **kwargs
    ):
        self.set_envs(envs)
        super().__init__(lang_in, lang_out, model)
        try:
            cred = credential.DefaultCredentialProvider().get_credential()
        except EnvironmentError:
            cred = credential.Credential(
                self.envs["TENCENTCLOUD_SECRET_ID"],
                self.envs["TENCENTCLOUD_SECRET_KEY"],
            )
        self.client = TmtClient(cred, "ap-beijing")
        self.req = TextTranslateRequest()
        self.req.Source = self.lang_in
        self.req.Target = self.lang_out
        self.req.ProjectId = 0

    def do_translate(self, text):
        self.req.SourceText = text
        resp: TextTranslateResponse = self.client.TextTranslate(self.req)
        return resp.TargetText


class AnythingLLMTranslator(BaseTranslator):
    name = "anythingllm"
    envs = {
        "AnythingLLM_URL": None,
        "AnythingLLM_APIKEY": None,
    }
    CustomPrompt = True

    def __init__(
        self, lang_out, lang_in, model, envs=None, prompt=None, ignore_cache=False
    ):
        self.set_envs(envs)
        super().__init__(lang_out, lang_in, model, ignore_cache)
        self.api_url = self.envs["AnythingLLM_URL"]
        self.api_key = self.envs["AnythingLLM_APIKEY"]
        self.headers = {
            "accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        self.prompttext = prompt

    def do_translate(self, text):
        messages = self.prompt(text, self.prompttext)
        payload = {
            "message": messages,
            "mode": "chat",
            "sessionId": "translation_expert",
        }

        response = requests.post(
            self.api_url, headers=self.headers, data=json.dumps(payload)
        )
        response.raise_for_status()
        data = response.json()

        if "textResponse" in data:
            return data["textResponse"].strip()


class DifyTranslator(BaseTranslator):
    name = "dify"
    envs = {
        "DIFY_API_URL": None,  # 填写实际 Dify API 地址
        "DIFY_API_KEY": None,  # 替换为实际 API 密钥
    }

    def __init__(
        self, lang_out, lang_in, model, envs=None, ignore_cache=False, **kwargs
    ):
        self.set_envs(envs)
        super().__init__(lang_out, lang_in, model, ignore_cache)
        self.api_url = self.envs["DIFY_API_URL"]
        self.api_key = self.envs["DIFY_API_KEY"]

    def do_translate(self, text):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "inputs": {
                "lang_out": self.lang_out,
                "lang_in": self.lang_in,
                "text": text,
            },
            "response_mode": "blocking",
            "user": "translator-service",
        }

        # 向 Dify 服务器发送请求
        response = requests.post(
            self.api_url, headers=headers, data=json.dumps(payload)
        )
        response.raise_for_status()
        response_data = response.json()

        # 解析响应
        return response_data.get("answer", "")


class ArgosTranslator(BaseTranslator):
    name = "argos"

    def __init__(self, lang_in, lang_out, model, ignore_cache=False, **kwargs):
        try:
            import argostranslate.package
            import argostranslate.translate
        except ImportError:
            logger.warning(
                "argos-translate is not installed, if you want to use argostranslate, please install it. If you don't use argostranslate translator, you can safely ignore this warning."
            )
            raise
        super().__init__(lang_in, lang_out, model, ignore_cache)
        lang_in = self.lang_map.get(lang_in.lower(), lang_in)
        lang_out = self.lang_map.get(lang_out.lower(), lang_out)
        self.lang_in = lang_in
        self.lang_out = lang_out
        argostranslate.package.update_package_index()
        available_packages = argostranslate.package.get_available_packages()
        try:
            available_package = list(
                filter(
                    lambda x: x.from_code == self.lang_in
                    and x.to_code == self.lang_out,
                    available_packages,
                )
            )[0]
        except Exception:
            raise ValueError(
                "lang_in and lang_out pair not supported by Argos Translate."
            )
        download_path = available_package.download()
        argostranslate.package.install_from_path(download_path)

    def translate(self, text: str, ignore_cache: bool = False):
        # Translate
        import argotranslate.translate  # noqa: F401

        installed_languages = (
            argostranslate.translate.get_installed_languages()  # noqa: F821
        )
        from_lang = list(filter(lambda x: x.code == self.lang_in, installed_languages))[
            0
        ]
        to_lang = list(filter(lambda x: x.code == self.lang_out, installed_languages))[
            0
        ]
        translation = from_lang.get_translation(to_lang)
        translatedText = translation.translate(text)
        return translatedText


class GrokTranslator(OpenAITranslator):
    # https://docs.x.ai/docs/overview#getting-started
    name = "grok"
    envs = {
        "GROK_API_KEY": None,
        "GROK_MODEL": "grok-2-1212",
    }
    CustomPrompt = True

    def __init__(
        self, lang_in, lang_out, model, envs=None, prompt=None, ignore_cache=False
    ):
        self.set_envs(envs)
        base_url = "https://api.x.ai/v1"
        api_key = self.envs["GROK_API_KEY"]
        if not model:
            model = self.envs["GROK_MODEL"]
        super().__init__(
            lang_in,
            lang_out,
            model,
            base_url=base_url,
            api_key=api_key,
            ignore_cache=ignore_cache,
        )
        self.prompttext = prompt


class GroqTranslator(OpenAITranslator):
    name = "groq"
    envs = {
        "GROQ_API_KEY": None,
        "GROQ_MODEL": "llama-3-3-70b-versatile",
    }
    CustomPrompt = True

    def __init__(
        self, lang_in, lang_out, model, envs=None, prompt=None, ignore_cache=False
    ):
        self.set_envs(envs)
        base_url = "https://api.groq.com/openai/v1"
        api_key = self.envs["GROQ_API_KEY"]
        if not model:
            model = self.envs["GROQ_MODEL"]
        super().__init__(
            lang_in,
            lang_out,
            model,
            base_url=base_url,
            api_key=api_key,
            ignore_cache=ignore_cache,
        )
        self.prompttext = prompt


class DeepseekTranslator(OpenAITranslator):
    name = "deepseek"
    envs = {
        "DEEPSEEK_API_KEY": None,
        "DEEPSEEK_MODEL": "deepseek-chat",
    }
    CustomPrompt = True

    def __init__(
        self, lang_in, lang_out, model, envs=None, prompt=None, ignore_cache=False
    ):
        self.set_envs(envs)
        base_url = "https://api.deepseek.com/v1"
        api_key = self.envs["DEEPSEEK_API_KEY"]
        if not model:
            model = self.envs["DEEPSEEK_MODEL"]
        super().__init__(
            lang_in,
            lang_out,
            model,
            base_url=base_url,
            api_key=api_key,
            ignore_cache=ignore_cache,
        )
        self.prompttext = prompt


class OpenAIlikedTranslator(OpenAITranslator):
    name = "openailiked"
    envs = {
        "OPENAILIKED_BASE_URL": None,
        "OPENAILIKED_API_KEY": None,
        "OPENAILIKED_MODEL": None,
    }
    CustomPrompt = True

    def __init__(
        self, lang_in, lang_out, model, envs=None, prompt=None, ignore_cache=False
    ):
        self.set_envs(envs)
        if self.envs["OPENAILIKED_BASE_URL"]:
            base_url = self.envs["OPENAILIKED_BASE_URL"]
        else:
            raise ValueError("The OPENAILIKED_BASE_URL is missing.")
        if not model:
            if self.envs["OPENAILIKED_MODEL"]:
                model = self.envs["OPENAILIKED_MODEL"]
            else:
                raise ValueError("The OPENAILIKED_MODEL is missing.")
        if self.envs["OPENAILIKED_API_KEY"] is None:
            api_key = "openailiked"
        else:
            api_key = self.envs["OPENAILIKED_API_KEY"]
        super().__init__(
            lang_in,
            lang_out,
            model,
            base_url=base_url,
            api_key=api_key,
            ignore_cache=ignore_cache,
        )
        self.prompttext = prompt


class QwenMtTranslator(OpenAITranslator):
    """
    Use Qwen-MT model from Aliyun. it's designed for translating.
    Since Traditional Chinese is not yet supported by Aliyun. it will be also translated to Simplified Chinese, when it's selected.
    There's special parameters in the message to the server.
    """

    name = "qwen-mt"
    envs = {
        "ALI_MODEL": "qwen-mt-turbo",
        "ALI_API_KEY": None,
        "ALI_DOMAINS": "This sentence is extracted from a scientific paper. When translating, please pay close attention to the use of specialized troubleshooting terminologies and adhere to scientific sentence structures to maintain the technical rigor and precision of the original text.",
    }
    CustomPrompt = True

    def __init__(
        self, lang_in, lang_out, model, envs=None, prompt=None, ignore_cache=False
    ):
        self.set_envs(envs)
        base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        api_key = self.envs["ALI_API_KEY"]

        if not model:
            model = self.envs["ALI_MODEL"]

        super().__init__(
            lang_in,
            lang_out,
            model,
            base_url=base_url,
            api_key=api_key,
            ignore_cache=ignore_cache,
        )
        self.prompttext = prompt

    @staticmethod
    def lang_mapping(input_lang: str) -> str:
        """
        Mapping the language code to the language code that Aliyun Qwen-Mt model supports.
        Since all existings languagues codes used in gui.py are able to be mapped, the original
        languague code will not be checked.
        """
        langdict = {
            "zh": "Chinese",
            "zh-TW": "Chinese",
            "en": "English",
            "fr": "French",
            "de": "German",
            "ja": "Japanese",
            "ko": "Korean",
            "ru": "Russian",
            "es": "Spanish",
            "it": "Italian",
        }

        return langdict[input_lang]

    def do_translate(self, text) -> str:
        """
        Qwen-MT Model reqeust to send translation_options to the server.
        domains are options, but suggested. it must be in English.
        """
        translation_options = {
            "source_lang": self.lang_mapping(self.lang_in),
            "target_lang": self.lang_mapping(self.lang_out),
            "domains": self.envs["ALI_DOMAINS"],
        }
        response = self.client.chat.completions.create(
            model=self.model,
            **self.options,
            messages=[{"role": "user", "content": text}],
            extra_body={"translation_options": translation_options},
        )
        return response.choices[0].message.content.strip()
