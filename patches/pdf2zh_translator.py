import csv
import hashlib
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

    def _cache_key_suffix(self, text: str) -> str:
        """[v23] 每段缓存键后缀钩子, 默认无。

        术语表翻译器覆写: 把"本段命中术语"的指纹编入键, 术语表局部修改
        只作废含该术语的段落, 不再整表失效(灵敏度实测: +1 行 dummy 术语
        曾致 23 段全 miss / 233s)。"""
        return ""

    def translate(self, text: str, ignore_cache: bool = False) -> str:
        """
        Translate the text, and the other part should call this method.
        :param text: text to translate
        :return: translated text
        """
        if not (self.ignore_cache or ignore_cache):
            cache = self.cache.get(text, self._cache_key_suffix(text))
            if cache is not None:
                # [自研补丁 2026-09-06] 缓存读取同样过机械伤清洗: v1 时代的缓存
                # 终稿可能带润色 LLM 重引入的半角标点/连字符(Melhani 实测全文
                # 868+410 处), 读取时清洗即可零成本修复, 无需重译。清洗幂等。
                cleaner = getattr(self, "_clean_cjk_typography", None)
                return cleaner(cache) if cleaner is not None else cache

        translation = self.do_translate(text)
        # 润色钩子: 若 translator 定义了 polish 方法, 对初译结果做二次润色
        polish = getattr(self, "polish", None)
        if polish is not None:
            try:
                translation = polish(text, translation)
            except Exception:
                logger.exception("Polish hook failed, fall back to raw translation.")
        # [自研补丁 2026-09-06] 润色输出同样过机械伤清洗: 实测 Melhani 摘要页
        # 残留 17 处(汉字后半角标点×9/汉字间连字符×5/半角括号×3)——润色 LLM
        # 重写稿重新引入, do_translate 内的清洗只保护初译, polish 返回值绕过它。
        # 清洗为纯函数且幂等, 重复应用无副作用; 其他 translator 无此方法则跳过。
        cleaner = getattr(self, "_clean_cjk_typography", None)
        if cleaner is not None:
            translation = cleaner(translation)
        self.cache.set(text, translation, self._cache_key_suffix(text))
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
        # [自研补丁 2026-09-03] 机械伤清洗版本号: 清洗规则变更后旧缓存自动失效
        # [2026-09-06 维持 v1] 读取路径已加清洗(见 translate), v1 缓存可直接复用,
        # 升 v2 反而会作废 Melhani 全部缓存导致整本重译, 故不再递增。
        self.add_cache_impact_parameters("typo_clean", "v1")
        self.think_filter_regex = re.compile(think_filter_regex, flags=re.DOTALL)
        # 润色钩子配置: POLISH=1 开启, POLISH_GLOSSARY 为可选术语表 CSV, POLISH_MODEL 可选指定润色模型
        self._polish_enabled = str(self.envs.get("POLISH", "")).lower() in ("1", "true", "on")
        self._polish_glossary_path = self.envs.get("POLISH_GLOSSARY") or None
        self._polish_glossary = None
        # 上下文窗口按线程隔离: 并发翻译时段落顺序互不污染
        self._tls = threading.local()
        # [自研补丁] 全文术语表: 必须与上下文窗口相反, 走实例级共享。
        # 上下文窗口讲究"就近", 应线程隔离; 术语表讲究"全局一致", 应跨线程共享。
        self._polish_terms = {}
        self._polish_terms_lock = threading.Lock()
        # 反思模式: 先审校批判, 再按建议修订 (吴恩达 translation-agent 三步流)
        self._polish_reflect = str(self.envs.get("POLISH_REFLECT", "")).lower() in ("1", "true", "on")
        # 确定性锚点断言: 数字保真+不译词保留, 不经 LLM 无盲区 (默认开, POLISH_ANCHOR=0 关闭)
        self._polish_anchor = str(self.envs.get("POLISH_ANCHOR", "1")).lower() in ("1", "true", "on")
        # [自研补丁] 公式占位符可读性增强 (默认关, POLISH_FORMULA_HINT=1 开启):
        # 允许在 {{vN}} 占位符后补一句简短中文说明其数学含义, 占位符本身仍原样保留。
        # 默认关闭是因为它会向译文插入额外文字, 可能改变版面; 需实测后再决定是否常开。
        self._polish_formula_hint = str(
            self.envs.get("POLISH_FORMULA_HINT", "0")).lower() in ("1", "true", "on")
        # [自研补丁] 公式占位符对照注入 (默认开, POLISH_FORMULA_MAP=0 关闭):
        # converter 将本段 {vN}->公式原文 的映射写入线程局部存储,
        # 润色 prompt 据此注入对照块, 使带公式句子的译文语序正确。
        self._polish_formula_map = str(
            self.envs.get("POLISH_FORMULA_MAP", "1")).lower() not in ("0", "false", "off")
        # [自研补丁] 领域标签: 告知模型"这是一篇什么领域的论文", 用于消解术语歧义。
        # 依据: EMNLP 2025 实证 —— 同一英文词在不同领域译法不同
        #       (例: system 在法律领域应译"体系"而非字面"系统"),
        #       加领域标签后 LLM 翻译正确; 论文结论是"关键在于如何有效利用领域信息"。
        # 默认关 (POLISH_DOMAIN=空), 需实测后决定是否常开。
        self._polish_domain = (self.envs.get("POLISH_DOMAIN") or "").strip()
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

        # [自研补丁 2026-09-03] 润色配置指纹进缓存键: 旧实现只登记了
        # polish/anchor/reflect 三个开关, POLISH_MODEL/DOMAIN/GUIDELINE/
        # FORMULA_MAP 开关及 terms.csv 内容变化后重翻仍命中旧缓存
        # ("改了配置/术语表没生效")。文件用 mtime+size+内容 md5 做指纹。
        if self._polish_enabled:
            def _polish_file_fingerprint(path):
                try:
                    if not path:
                        return "unset"
                    st = os.stat(path)
                    digest = hashlib.md5()
                    with open(path, "rb") as fh:
                        for chunk in iter(lambda: fh.read(65536), b""):
                            digest.update(chunk)
                    return f"{int(st.st_mtime)}_{st.st_size}_{digest.hexdigest()[:12]}"
                except Exception:
                    return "missing"

            self.add_cache_impact_parameters(
                "polish_model", str(self.envs.get("POLISH_MODEL") or ""))
            self.add_cache_impact_parameters(
                "polish_domain", self._polish_domain or "")
            self.add_cache_impact_parameters(
                "polish_formula_map", "on" if self._polish_formula_map else "off")
            self.add_cache_impact_parameters(
                "polish_formula_hint", "on" if self._polish_formula_hint else "off")
            self.add_cache_impact_parameters(
                "polish_glossary_fp",
                _polish_file_fingerprint(self._polish_glossary_path))
            self.add_cache_impact_parameters(
                "polish_guideline_fp",
                _polish_file_fingerprint(self._polish_guideline_path))

        # [v23] 术语表指纹渐进化: 整表 fp 从静态键移除(它使术语表 +1 行就
        # 全文缓存失效, 灵敏度实测 23 段/233s), 改由 _cache_key_suffix 把
        # "本段命中术语"指纹按段编入键。移除前快照当前形态为旧版回查键,
        # 既有条目经回查继续命中(表未变时), 零重译迁移。
        if self._polish_enabled and self._polish_glossary_path:
            self.cache.snapshot_legacy_key()
            self.cache.remove_param("polish_glossary_fp")

    def _cache_key_suffix(self, text: str) -> str:
        """[v23] 术语表按段指纹: 只哈希本段命中的术语条目。

        与 prompt() 注入用同一套归一化匹配(_norm 剥离 {vN} 与非字母数字),
        保证"注入了什么"与"键里编了什么"一致。未命中任何术语的段落用
        显式空标记(与无后缀的旧形态键区分开)。"""
        if not (self._polish_enabled and self._polish_glossary_path):
            return ""
        glossary = self._polish_glossary
        if glossary is None and self._polish_glossary_path:
            glossary = self._polish_glossary = self._load_polish_glossary()
        if not glossary:
            return ""

        def _norm(s: str) -> str:
            return re.sub(r"[^a-z0-9]", "", s.lower())

        text_norm = _norm(re.sub(r"\{v\d+\}", "", text or ""))
        matched = sorted(
            (k, v) for k, v in glossary.items()
            if _norm(k) and _norm(k) in text_norm
        )
        if not matched:
            return "#g23:-"
        blob = json.dumps(matched, ensure_ascii=False, sort_keys=True)
        return "#g23:" + hashlib.md5(blob.encode("utf-8")).hexdigest()[:10]

    @property
    def _polish_context(self):
        """线程局部的上下文窗口 (并发翻译时每条线程独立维护自己的前文)."""
        if not hasattr(self._tls, "polish_ctx"):
            self._tls.polish_ctx = deque(maxlen=3)
        return self._tls.polish_ctx

    # --------------------------------------------------------------
    # [自研补丁 2026-09-03] 中文排版机械伤清洗 (确定性正则, 不经 LLM, 幂等):
    # 治两类高频观感伤 —— ① 汉字间残留连接符(PDF 断行词/合成词直译):
    # "鱼类-式"→"鱼式"、"模型 - 化"→"模型化"; ② 汉字邻接的半角标点残留/
    # 双标点: "变形,产生"→"变形，产生"、"系统.等"→"系统。等"、",，"→"，"。
    # 负面清单(不触发): 数字小数点(3.12)、数字范围(1-3)、{vN} 公式占位符、
    # 纯英文内部标点 —— 所有规则均要求至少一侧紧邻汉字。
    @staticmethod
    def _clean_cjk_typography(text: str) -> str:
        if not text or not re.search(r"[\u4e00-\u9fff]", text):
            return text
        c = text
        # ① 汉字-汉字(允许中间空白)的连接符删除; 英文复合词不受影响
        c = re.sub(r"(?<=[\u4e00-\u9fff])\s*-\s*(?=[\u4e00-\u9fff])", "", c)
        # ②a 汉字后(允许隔空白)紧跟的半角标点转全角 (左侧为数字的小数点不触发)
        # [2026-09-10] lookbehind 扩 "}"——占位符闭括号后的半角逗号
        # ("…矩阵{v0}, ∥C∥…")此前逃过汉字断言, 实测残留 12 处。
        # 安全性: 本函数入口已要求段落含汉字, }后逗号在汉字语境下
        # 均为中文列举语义; 纯英文段在入口即被跳过。
        c = re.sub(r"(?<=[\u4e00-\u9fff}])\s*,\s*", "，", c)
        c = re.sub(r"(?<=[\u4e00-\u9fff}])\s*;\s*", "；", c)
        c = re.sub(r"(?<=[\u4e00-\u9fff}])\s*:\s*", "：", c)
        c = re.sub(r"(?<=[\u4e00-\u9fff}])\s*\?\s*", "？", c)
        c = re.sub(r"(?<=[\u4e00-\u9fff}])\s*!\s*", "！", c)
        # 句点: 汉字后(允许隔空白)的 '.' 仅当其后不是字母/数字时判为句尾残留
        c = re.sub(r"(?<=[\u4e00-\u9fff])\s*\.(?![0-9A-Za-z])", "。", c)
        # ②b 汉字前紧贴的半角逗号/分号 (如 ",但")
        c = re.sub(r"\s*,\s*(?=[\u4e00-\u9fff])", "，", c)
        c = re.sub(r"\s*;\s*(?=[\u4e00-\u9fff])", "；", c)
        # ③ 含汉字的半角括号对转全角 (公式/纯英文括号不受影响)
        c = re.sub(r"\(([^()]*[\u4e00-\u9fff][^()]*)\)", r"（\1）", c)
        # ④ 标点归一: 连续同类标点收敛 + 常见错序对修正
        c = re.sub(r"([，。；：！？、])\1+", r"\1", c)
        c = re.sub(r"，、", "、", c)
        c = re.sub(r"([。！？])，", r"\1", c)
        # [自研补丁 2026-09-10 v22] 句中逗号/顿号/分号/冒号后紧跟句号("，。")
        # → 仅留句号(Melhani 实测渲染层 14 处; 缓存内预防性收敛)。
        c = re.sub(r"[，、；：]\s*。", "。", c)
        # 同向全角括号相邻叠加(LLM 重排句式自加括号 × 原文括号) → 单层。
        # 学术文本不存在同向括号嵌套, 收敛安全。
        c = re.sub(r"（(?:\s*（)+", "（", c)
        c = re.sub(r"）(?:\s*）)+", "）", c)
        return c

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
        # [自研补丁 2026-09-03] 机械伤清洗: 初译入库前整形(不开润色也生效)
        return self._clean_cjk_typography(content)

    def prompt(
        self, text: str, prompt_template: Template | None = None
    ) -> list[dict[str, str]]:
        """[自研补丁 2026-09-07] 初译术语表注入。

        背景: 术语表原先只进润色 prompt, 初译(默认模板)对术语表一无所知;
        润色修订虽能纠正术语误译, 但修订稿可能被占位符保护/锚点断言回退到
        初译(占位符密集段高发), 于是终稿落回"从未见过术语表"的初译
        (实例: Melhani 标题 in-silico → "硅基准验证")。
        修法: 在初译 user 消息前注入**与本段文本匹配的**术语条目 ——
        初译第一次就译对, 回退也无害。按需注入控制增量 token(全表仅
        注入命中条目); 不开润色(纯初译)时同样生效。
        缓存一致性: 术语表内容通过 init 时登记的 polish_glossary_fp 进入
        缓存键, 表更新后旧缓存自动失效。"""
        messages = super().prompt(text, prompt_template)
        try:
            # getattr 防御: init 期间 add_cache_impact_parameters("prompt", ...)
            # 会先于 _polish_* 属性赋值调用本方法, 此时静默跳过注入。
            glossary = getattr(self, "_polish_glossary", None)
            glossary_path = getattr(self, "_polish_glossary_path", None)
            if glossary is None and glossary_path:
                self._polish_glossary = glossary = self._load_polish_glossary()
            if glossary and messages:
                # 归一化匹配: 占位符会把术语隔断(实例: In{v3}Silico 匹配不到
                # in-silico), 故对两侧先剥离 {vN} 再压缩为 [a-z0-9] 比较。
                def _norm(s: str) -> str:
                    return re.sub(r"[^a-z0-9]", "", s.lower())

                text_norm = _norm(re.sub(r"\{v\d+\}", "", text or ""))
                terms = "\n".join(
                    f"- {k} => {v}"
                    for k, v in glossary.items()
                    if _norm(k) and _norm(k) in text_norm
                )
                if terms:
                    block = (
                        "Glossary of mandatory translations (use exactly these "
                        "translations, keep them consistent; a {vN} placeholder "
                        "inside a term is part of that term):\n"
                        f"{terms}\n\n"
                    )
                    for m in messages:
                        if m.get("role") == "user":
                            m["content"] = block + m["content"]
                            break
        except Exception:
            logging.exception("Init-prompt glossary injection failed, ignore it.")
        return messages

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

    def _formula_readable(self, text: str) -> str:
        """[自研补丁] 报告可读化: 把 {vN} 占位符替换为 ⟨公式: 原文⟩, 供人工终审阅读。

        对照表来自 converter 注入的线程局部存储(本段翻译时有效); 表里没有的
        占位符保持原样。仅用于报告展示, 不影响任何翻译/校验逻辑。
        """
        fmap = getattr(self._tls, "formula_map", None) or {}
        if not fmap:
            return text
        def _sub(m):
            v = fmap.get(m.group(0))
            return "⟨公式: %s⟩" % v if v else m.group(0)
        return re.sub(r"\{v\d+\}", _sub, text)

    def _polish_report_append(self, source: str, draft: str, critique: str, revised: str):
        """审校报告落盘: 原文/初译/建议/修订 四栏对照, 供人工终审."""
        if not self._polish_report_file:
            return
        try:
            os.makedirs(self._polish_report_dir, exist_ok=True)
            self._polish_report_seq += 1
            with open(self._polish_report_file, "a", encoding="utf-8") as f:
                f.write(f"## 段落 {self._polish_report_seq}\n\n")
                f.write(f"**原文**\n\n{self._formula_readable(source)}\n\n")
                f.write(f"**初译**\n\n{self._formula_readable(draft)}\n\n")
                f.write(f"**审校建议**\n\n{critique}\n\n")
                f.write(f"**自动修订**\n\n{self._formula_readable(revised)}\n\n---\n\n")
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
                f.write(f"**原文**\n\n{self._formula_readable(source)}\n\n"
                        f"**初译(保持未动)**\n\n{self._formula_readable(draft)}\n\n")
                f.write(f"**存疑点**\n\n{doubt}\n\n---\n\n")
        except Exception:
            logger.exception("Failed to write doubt list.")

    # [自研补丁] 中文数字对照表, 用于锚点断言识别合法数字转换
    _CN_DIGIT_MAP = {
        "0": "零", "1": "一", "2": "二", "3": "三", "4": "四",
        "5": "五", "6": "六", "7": "七", "8": "八", "9": "九",
    }

    def _num_in_text(self, num: str, text: str) -> bool:
        """判断数字 num 是否出现在 text 中。

        [自研补丁] 除字面匹配外, 还接受两类等价形式, 避免合法数字转换被误判为
        "数字缺失"(误报会让本已正确的译文被记入违规, 干扰人工核查):
          ① 去前导零: 02 -> 2   (July 02-05 -> 7月2日-5日)
          ② 中文数字: 3  -> 三  (3D -> 三维)
        """
        if num in text:
            return True
        # ① 去前导零后匹配
        stripped = num.lstrip("0")
        if stripped and stripped in text:
            return True
        # ② 逐位中文数字匹配 (仅当每一位都有对应汉字时成立)
        if all(ch in self._CN_DIGIT_MAP for ch in num):
            cn = "".join(self._CN_DIGIT_MAP[ch] for ch in num)
            if cn in text:
                return True
        return False

    def _anchor_check(self, source: str, content: str):
        """确定性锚点断言 (不经 LLM, 无盲区). 返回 (不译词丢失列表, 数字缺失列表).
        检查项: ①数字保真 ②不译词保留(术语表中 target==source 的条目)."""
        # 剔除占位符与文献引用括号(如 [15–17, 24]), 避免区间端点误报
        # [自研补丁 2026-09-02 修正] 原正则 \{\{v\d+\}\} 匹配双大括号, 但 converter.py
        # 实际生成的是单大括号 {vN}(f-string "{{" 转义), 4 处正则全部失配=形同虚设,
        # 锚点剔除与占位符保护从未生效。统一改为 \{v\d+\}。
        src_clean = re.sub(r"\{v\d+\}", " ", source)
        src_clean = re.sub(r"\[[^\]]*\]", " ", src_clean)
        out_clean = re.sub(r"\{v\d+\}", " ", content)
        # ① 数字保真: 原文数字必须全部出现在译文中 (千分位归一化)
        src_nums = set(
            n.replace(",", "")
            for n in re.findall(r"\d[\d,]*(?:\.\d+)?", src_clean)
        )
        out_norm = out_clean.replace(",", "")
        missing = sorted(n for n in src_nums if not self._num_in_text(n, out_norm))
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
        # [自研补丁] 全文术语一致性: 累积"本文已采用的译名"。
        # 原实现只把"前 2 段原文+译文"作上下文(deque maxlen=3), 窗口太窄,
        # 导致同一术语在相隔较远的段落里译法漂移(如 柔顺机构/CM 混用)。
        #
        # 2026-09-02 修正: 原实现挂在 self._tls(线程局部) 上, 而段落是被
        # ThreadPoolExecutor(max_workers=thread_num, 默认 8) 并发处理的,
        # 于是 8 个线程各自只累积 1/8, 所谓"全文"名不副实。
        # 改为实例级共享 + 互斥锁, 使任一线程发现的术语立即对所有线程可见。
        # 注: 字典读写在 CPython 下有 GIL 保护不会损坏结构, 但仍加锁以确保
        #     (a) 读到的 term_memory 是完整一致快照; (b) 不依赖 GIL 实现细节。
        term_seen = self._polish_terms
        with self._polish_terms_lock:
            for k, v in glossary.items():
                if k.lower() in source.lower():
                    term_seen[k] = v
            term_memory = "\n".join(
                f"- {k} => {v}" for k, v in term_seen.items()
            )
        context = "\n".join(
            f"[前文{i+1}] 原文: {s}\n[前文{i+1}] 译文: {t}"
            for i, (s, t) in enumerate(self._polish_context)
        )
        guideline_block = (
            f"翻译指南(必须遵守):\n{self._polish_guideline_text}\n\n"
            if self._polish_guideline_text else ""
        )
        # 全文已用译名(优先级低于术语表, 用于消除跨段落译名漂移)
        term_memory_block = (
            f"本文已采用的译名(全文须保持一致, 与术语表冲突时以术语表为准):\n{term_memory}\n\n"
            if term_memory else ""
        )
        # [自研补丁] 领域标签: 置于最前, 让模型先建立"这是什么领域的文本"的认知,
        # 再读待译段落 —— 顺序有讲究, 领域信息应先于具体内容进入上下文。
        domain_block = (
            f"本文所属领域: {self._polish_domain}\n"
            "请按该领域的通行译法与表达习惯处理术语; 遇到多义词时, "
            "优先采用该领域的惯用译法, 而非字面直译。\n\n"
            if self._polish_domain else ""
        )
        # [自研补丁] 公式占位符对照表: converter 在调用 translate 前把本段 {vN} 与其
        # 原文(字符流)写入线程局部存储。据此告知 LLM 每个占位符的数学含义, 使带公式
        # 句子的译文语序正确(例: {v8}0{v9}1{v10} = "0 ≤ v ≤ 1" 时可译成通顺的中文句式)。
        # 占位符本身必须原样保留 —— 这一点同时由"占位符保护"(破坏即回退初译)硬性兜底。
        formula_map = getattr(self._tls, "formula_map", None) or {}
        formula_block = ""
        if formula_map and self._polish_formula_map:
            _rows = "\n".join(
                "  %s = %s" % (k, v if v else "(空)")
                for k, v in formula_map.items()
            )
            formula_block = (
                "本段公式占位符对照(供理解句意; 译文中占位符必须原样保留, 位置可按中文语序调整):\n"
                + _rows + "\n\n"
            )
        base_block = (
            domain_block
            + formula_block
            + term_memory_block
            + self._polish_blocks(source, target, terms, context)
        )
        # [自研补丁 2026-09-03] 排版规则提示(双保险之一): 确定性清洗
        # _clean_cjk_typography 在译文出院前兜底, 此规则让 LLM 从源头少产出
        typo_rule = (
            "排版要求: 中文译文中不得残留半角标点(如 , . ; : ? !); 汉字之间不得出现连接符\"-\", "
            "原文因 PDF 断行被拆开的英文合成词(如 fish-like 被拆成 fish- 与 like)应合并还原后再翻译。\n"
        )
        # [自研补丁] 公式占位符可读性提示 (POLISH_FORMULA_HINT=1 时启用)
        formula_hint_rule = (
            "6. 若原文含 {v数字} 占位符(公式片段), 可在该占位符紧邻处用全角括号补一句极简短的"
            "中文说明其数学含义(例: {v8}0{v9}1{v10} 后补'（体积分数介于0与1之间）'), "
            "使句子可读; 占位符本身必须原样保留, 不得改写/移动/删除, 且不得臆造原文无依据的数值。\n"
            if self._polish_formula_hint else ""
        )

        if self._polish_reflect:
            # 阶段1: 审校批判 (忠实性优先, 流畅性其次)
            # [自研补丁 2026-09-07] 输出纪律: 早期版本不限长度, 批判输出人均
            # 2.5K token(审校报告 266 段写出 1.2MB), 绝大多数是复述原文的低价值
            # 建议; 且批判几乎从不"无问题" → 修订调用几乎必发。限流后批判输出
            # 预期降至 ~600 token, 全链省 ~40% token。
            critique_sys = (
                "你是资深学术译审, 对机器初译进行严格审校。只输出修改建议清单, 分三组:\n"
                "【忠实性】漏译/加译/错译/数字单位错误/逻辑扭曲, 逐条给出位置和改法;\n"
                "【流畅性】术语不一致/指代不清/欧化句式/生硬表达, 逐条给出位置和改法;\n"
                # [自研补丁] 存疑判定收窄: 原措辞导致 LLM 把"术语表未收录"一律记为存疑,
                # 产生大量标准译法(如 topology optimization=拓扑优化)的低价值条目,
                # 使存疑清单膨胀到数十KB, 反而加重人工审阅负担。
                "【存疑】仅限以下两类, 务必严格、宁缺毋滥:\n"
                "  ① 术语表译名与本文上下文明显冲突(表内译名在此语境下讲不通);\n"
                "  ② 该英文术语在学界存在两种以上通行中文译法, 且本文语境不足以判定取舍。\n"
                "不列入: 术语表未收录但译法属学界标准译法的词条(例: topology optimization=拓扑优化);\n"
                "不列入: 占位符导致的语义不完整; 不列入: 一般性措辞优劣。\n"
                "若无符合上述条件者, 此组输出: 无存疑\n"
                "不输出译文本身。若三组均无内容, 只输出: 无明显问题\n"
                "输出纪律(必须遵守): 每组最多 3 条、按影响程度排序; 每条不超过 60 字, "
                "格式为\"位置 → 改法\"; 严禁复述原文或译文整句来\"说明问题\", "
                "只写最小可执行的修正指令。"
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
                    "5. 只输出修订后的译文, 不要任何解释或前后缀。\n"
                    + typo_rule
                    + formula_hint_rule
                )
                content = self._polish_llm([
                    {"role": "system", "content": revise_sys},
                    {"role": "user", "content": (
                        guideline_block
                        + f"审校建议(逐条落实):\n{critique}\n\n"
                        # [自研补丁 2026-09-07] 修订瘦身: 不再重复注入前文 context
                        # (批判阶段已看过, 修订是局部改写用不到跨段上下文),
                        # 只保留 领域+公式对照+术语记忆+原文+初译 —— 省去每次
                        # 修订调用约 1-2K 输入 token。
                        + self._polish_blocks(source, target, terms, "")
                        + "修订后的译文:"
                    )},
                ])
        else:
            critique_txt = ""
            sys_prompt = (
                "你是资深学术译者, 对机器初译结果做最终润色。要求:\n"
                "1. 只润色, 不改写意思, 不增删信息; 使用正式、严谨、流畅的学术语言。\n"
                "2. 严禁修改任何形如 {{v数字}} 的占位符(公式/富文本标记), 必须原样保留, 数量与编号都不能变。\n"
                "3. 专业术语译法必须与术语表一致; 与前文译文中已采用的译法保持前后一致。\n"
                "4. 只输出润色后的译文, 不要任何解释或前后缀。\n"
                + typo_rule
                + formula_hint_rule
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

        # 占位符保护: 润色若破坏 {vN} 占位符, 回退初译
        # [自研补丁 2026-09-02 修正] 正则同上修正(原双大括号失配, 此保护从未生效)
        src_ph = set(re.findall(r"\{v\d+\}", target))
        dst_ph = set(re.findall(r"\{v\d+\}", content))
        if src_ph != dst_ph:
            logger.warning("Polish broke placeholders, fall back to raw translation.")
            content = target
        # [自研补丁 2026-09-03] 机械伤清洗: 润色/回退初译后的最终整形(幂等)
        content = self._clean_cjk_typography(content)
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
