import logging
import os
import json
import re
from peewee import Model, SqliteDatabase, AutoField, CharField, TextField, SQL
from typing import Optional


# we don't init the database here
db = SqliteDatabase(None)
logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"\{v\d+\}")

# [v27] 已退役的键参数前缀: 润色钩子(POLISH)退场后, 其键参数不再区分缓存世代。
#
# 背景: polish / polish_anchor / polish_reflect / polish_model / polish_domain /
# polish_formula_map / polish_formula_hint / polish_glossary_fp /
# polish_guideline_fp 这 9 个键曾参与缓存键(translator L518-564)。把 POLISH 置 0
# 后这些键不再写入 —— 若不设回查通道, 既有译文(键里带 polish*)会**整篇失配重译**,
# 且豆包回灌进库的定稿译文会被新译者覆盖。
#
# 为什么不用 snapshot_legacy_key()(v23 的"渐进化键改造"通道): 那条路要求把某个
# 历史键形态原样登记下来回查, 而润色世代的形态**无法重建** —— 其中
# polish_guideline_fp 是"每篇论文一份的翻译指南"文件指纹, 随文档而变; 且库里
# 同时存在 pre-v23(带 polish_glossary_fp) 与 post-v23(该键已被 remove_param 移除)
# 两种形态, ladder 的 prev/legacy 两个槽位也覆盖不全。
#
# 故改为"投影": 按前缀把退役参数从世代身份里整体剔除 —— 库中带 polish* 的行按
# 剔除后的形态登记进 _retired_index, 当前世代(不含 polish*)命中后回写迁移到当前
# 形态(与 v23/v24-A 的 ladder 同约定), 实现零重译迁移。
#
# 同批退役的还有 v23s(术语表"按段指纹"后缀): 它的生产者是润色术语表
# (_cache_key_suffix 在 POLISH/术语表未启用时恒返回 ""), 润色退场后该维度不再产生,
# 而库里的行几乎都带它 —— 不剔除则投影形态仍差一维, 一行也命中不了。
# 两者都只由润色子系统生产, 故一并按"退役维度"处理: 退役世代重新只按
# "渲染所必需的维度"(引擎参数 + 文档画像 fp 等)区分缓存。
_RETIRED_PARAM_PREFIXES = ("polish",)
_RETIRED_PARAM_KEYS = ("v23s",)

# [v28.21] "文档画像"维度的键名: doc_summary_fp = f(首页文本 md5, 摘要内容 md5)。
#
# 为什么它需要一条专门的回查通道: 摘要的落盘键与这个 fp 都由"首页拼接文本"
# 算出, 而 v28.21 之前那段文本里带着 {vN} 占位符 —— 占位符集合由 config
# (formular_char_pattern / formular_font_pattern)决定, 于是**改一次配置 =
# 摘要键漂移 = 全篇缓存换键重译**(实测 CLAP: 491 秒, 且重译结果盖掉了豆包
# 回路已采纳的译文)。v28.21 已在源头把 {vN} 还原成真实字形(converter)并给
# 摘要键加了防(translator._summary_text_key), 但库里已经躺着几代不同 fp 的
# 行(CLAP 的 8bff… / 38bc…), 不救的话下次渲染仍要整篇重译。
_DOC_SUMMARY_PARAM = "doc_summary_fp"


class _TranslationCache(Model):
    id = AutoField()
    translate_engine = CharField(max_length=20)
    translate_engine_params = TextField()
    original_text = TextField()
    translation = TextField()

    class Meta:
        database = db
        constraints = [
            SQL(
                """
            UNIQUE (
                translate_engine,
                translate_engine_params,
                original_text
                )
            ON CONFLICT REPLACE
            """
            )
        ]


class TranslationCache:
    @staticmethod
    def _sort_dict_recursively(obj):
        if isinstance(obj, dict):
            return {
                k: TranslationCache._sort_dict_recursively(v)
                for k in sorted(obj.keys())
                for v in [obj[k]]
            }
        elif isinstance(obj, list):
            return [TranslationCache._sort_dict_recursively(item) for item in obj]
        return obj

    def __init__(self, translate_engine: str, translate_engine_params: dict = None):
        assert (
            len(translate_engine) < 20
        ), "current cache require translate engine name less than 20 characters"
        self.translate_engine = translate_engine
        self.legacy_params_json = None
        self.prev_params_json = None
        self._legacy_canon_index = None
        self._retired_canon_index = None      # [v27] 退役参数投影索引(惰性)
        self._reduced_canon_index = None      # [v28.21] 最小世代身份索引(惰性)
        self.replace_params(translate_engine_params)

    # The program typically starts multi-threaded translation
    # only after cache parameters are fully configured,
    # so thread safety doesn't need to be considered here.
    def replace_params(self, params: dict = None):
        if params is None:
            params = {}
        self.params = params
        params = self._sort_dict_recursively(params)
        self._sorted_params = params
        self.translate_engine_params = json.dumps(params)

    def update_params(self, params: dict = None):
        if params is None:
            params = {}
        self.params.update(params)
        self.replace_params(self.params)

    def add_params(self, k: str, v):
        self.params[k] = v
        self.replace_params(self.params)

    def remove_param(self, k: str):
        """[v23] 移除静态键参数 (配合 snapshot_legacy_key 做渐进化键改造)。"""
        if k in self.params:
            self.params.pop(k)
            self.replace_params(self.params)

    def snapshot_legacy_key(self):
        """[v23] 把当前参数形态登记为旧版回查形态 (在移除渐进化参数之前调用)。
        旧缓存条目按此形态命中, 实现零重译迁移。"""
        self.legacy_params_json = self.translate_engine_params
        self._legacy_canon_index = None   # 惰性构建

    def snapshot_prev_key(self):
        """[v24-A] 登记当前参数形态为"上代形态" (在加入新一代键参数前调用)。

        与 legacy 的区别: prev 是紧接着的上一代 (如 doc_summary_fp 加入前的
        形态, 即 v23 世代条目所在形态), legacy 是更早的大版本形态。get() 会
        依次回查两种形态, 渐进化改造不再只有一层。"""
        self.prev_params_json = self.translate_engine_params
        self._legacy_canon_index = None   # 索引需含新旧两代, 重建

    def _legacy_index(self):
        """[v23] 旧条目规范化索引 (惰性, 每引擎一次)。

        旧条目以 raw 文本入库, sqlite 精确匹配无法用"规范化查询串"找到它们
        —— 而编号平移场景恰恰是"规范化相等、原文不等"。此处把本引擎全部
        旧行 canon 化载入内存: (params_json, 规范文本) → (原文, 译文)。
        4 千行量级构建 <1s, 内存 ~数 MB。"""
        if self._legacy_canon_index is not None:
            return self._legacy_canon_index
        idx = {}
        try:
            for row in _TranslationCache.select().where(
                _TranslationCache.translate_engine == self.translate_engine
            ):
                canon_text, _ = self._canon_seq(row.original_text)
                idx[(row.translate_engine_params, canon_text)] = (
                    row.original_text, row.translation)
        except Exception as e:
            logger.debug(f"Legacy canon index build failed: {e}")
        self._legacy_canon_index = idx
        return idx

    def _params_json(self, key_suffix: str) -> str:
        """[v23] 每段后缀 → 参数键形态。空后缀保持原 JSON (与旧条目字节一致)。"""
        if not key_suffix:
            return self.translate_engine_params
        params = dict(self._sorted_params)
        params["v23s"] = key_suffix
        return json.dumps(self._sort_dict_recursively(params))

    def _with_suffix(self, base_json: str, key_suffix: str) -> str:
        """[v24-A] 给历史(prev/legacy)基形态补上每段 v23s 后缀。

        库里的行是以"基形态 + v23s"入库的(如 '#g23:-'), 而 snapshot 登记的
        只是基形态 —— 不补后缀则 exact 匹配与内存索引双双落空。"""
        if not key_suffix or not base_json:
            return base_json
        try:
            params = json.loads(base_json)
        except Exception:
            return base_json
        if not isinstance(params, dict):
            return base_json
        params["v23s"] = key_suffix
        return json.dumps(self._sort_dict_recursively(params))

    @staticmethod
    def _strip_retired(base_json: str):
        """[v27] 剔除退役键参数(_RETIRED_PARAM_PREFIXES)后的形态。

        返回 (形态, 剔除个数)。无退役参数时**原样返回**并报 0 —— 保证当前世代
        的键形态逐字节不变(不引入无谓重排序)。形态用同一套递归排序序列化,
        以便与被剔除后的 self.translate_engine_params 逐字节比较。
        """
        if not base_json:
            return base_json, 0
        try:
            params = json.loads(base_json)
        except Exception:
            return base_json, 0
        if not isinstance(params, dict):
            return base_json, 0
        kept = {
            k: v for k, v in params.items()
            if not (k in _RETIRED_PARAM_KEYS
                    or any(k.startswith(p) for p in _RETIRED_PARAM_PREFIXES))
        }
        n = len(params) - len(kept)
        if not n:
            return base_json, 0
        return json.dumps(TranslationCache._sort_dict_recursively(kept)), n

    def _retired_index(self):
        """[v27] "剔除退役参数后"的条目索引 (惰性, 每引擎一次)。

        只登记确实带退役参数的行(带 polish* 的润色世代), 故与当前世代的行
        互不覆盖。内存索引而非 SQL: 旧行以 raw 文本入库, sqlite 精确匹配找不到
        "规范化相等、原文不等"的行(与 _legacy_index 同理)。按 id 升序后写覆盖
        —— 同一段落在润色世代里被翻过多次时, 留最新一条。
        """
        if self._retired_canon_index is not None:
            return self._retired_canon_index
        idx = {}
        try:
            for row in (_TranslationCache.select()
                        .where(_TranslationCache.translate_engine
                               == self.translate_engine)
                        .order_by(_TranslationCache.id)):
                stripped, n = self._strip_retired(row.translate_engine_params)
                if not n:
                    continue
                canon_text, _ = self._canon_seq(row.original_text)
                idx[(stripped, canon_text)] = (row.original_text, row.translation)
        except Exception as e:
            logger.debug(f"Retired-param canon index build failed: {e}")
        self._retired_canon_index = idx
        return idx

    @staticmethod
    def _strip_docsummary(base_json: str):
        """[v28.21] 剔除"文档画像"(_DOC_SUMMARY_PARAM)维度后的形态。

        摘要键漂移的成因见 _DOC_SUMMARY_PARAM 处注释。这里把该维度从世代身份
        里整体剔除, 任一世代的行都投影到同一形态 —— 与 v27 剔除 polish* 同一
        思路, 只是这次退役的是"文档画像"这一维度。

        放宽是可接受的: 文档画像只是每段 prompt 里 ≤120 字的软提示, 而其余
        维度(引擎/语向/模型/prompt/template/术语表 fp/typo_clean…)仍要求逐
        字节相同 —— 不是"随便什么旧译文都拿来用"。返回 (形态, 是否剔除)。
        """
        if not base_json:
            return base_json, False
        try:
            params = json.loads(base_json)
        except Exception:
            return base_json, False
        if not isinstance(params, dict) or _DOC_SUMMARY_PARAM not in params:
            return base_json, False
        kept = {k: v for k, v in params.items() if k != _DOC_SUMMARY_PARAM}
        return json.dumps(TranslationCache._sort_dict_recursively(kept)), True

    def _reduced_form(self, base_json: str) -> str:
        """[v28.21] "不含文档画像的世代身份": 只剔除 doc_summary_fp 这一维。

        **不**顺手剔除 v23s/polish*: 那些维度属于 ③ 的职权(且 ③ 有自己的门控)。
        这里保持它们原样, 世代隔离就自动成立 —— 润色世代的行带 polish*, 当前
        无润色的任务形态里没有它, 两者天然对不上, 不需要额外门控。
        """
        return self._strip_docsummary(base_json)[0]

    @staticmethod
    def _has_retired_generation(base_json: str) -> bool:
        """[v28.21] 当前世代是否带**退役世代标记**(polish* 前缀)。

        门控(③④)原先用 `_strip_retired(pj)[1] != 0` 判断, 但那把 v23s 也算进去
        了 —— 而 v23s **不是世代标记**: 前瞻(v23.4)至今仍在生产 `#la:…` 后缀。
        于是"带前瞻的段"被误判成润色世代 → 门控一关, 退役投影与文档画像投影
        双双失效。实测: CLAP 每代 13~14 段前瞻段因此救不回来(182/195 → 应 195/195)。

        门控真正要回答的是"当前任务是否重新开启了润色" —— 只有那一种情况才需要
        世代严格隔离(不把未润色的旧译文喂给开启润色的任务)。v23s 不改变这个判断:
        它在两个世代里都只是个"本段指纹"后缀, 且已由投影剔除。
        """
        if not base_json:
            return False
        try:
            params = json.loads(base_json)
        except Exception:
            return False
        if not isinstance(params, dict):
            return False
        return any(k.startswith(p)
                   for k in params for p in _RETIRED_PARAM_PREFIXES)

    def _reduced_index(self):
        """[v28.21] "不含文档画像的世代身份"索引 (惰性, 每引擎一次)。

        只登记**确实带 doc_summary_fp**的行 —— 不带该维度的行在 ladder ①②里
        本就能对上, 无需在此重复。按 id 升序后写覆盖: 同一段落在不同世代里被
        翻过多次时留最新一条, 与 _legacy_index/_retired_index 同约定。
        用内存索引而非 SQL 的理由同 _legacy_index(旧行以 raw 文本入库, sqlite
        精确匹配找不到"规范化相等、原文不等"的行)。
        """
        if self._reduced_canon_index is not None:
            return self._reduced_canon_index
        idx = {}
        try:
            for row in (_TranslationCache.select()
                        .where(_TranslationCache.translate_engine
                               == self.translate_engine)
                        .order_by(_TranslationCache.id)):
                stripped, had = self._strip_docsummary(row.translate_engine_params)
                if not had:
                    continue
                canon_text, _ = self._canon_seq(row.original_text)
                idx[(stripped, canon_text)] = (row.original_text, row.translation)
        except Exception as e:
            logger.debug(f"Reduced-form canon index build failed: {e}")
        self._reduced_canon_index = idx
        return idx

    @staticmethod
    def _canon_seq(text: str):
        """[v23] {vN} 全局编号 → 段内出现序规范编号。

        背景: var 编号是文档级顺序分配, 公式判定(vfont/vchar)的任何微调都会
        让后续所有段落编号平移, 缓存键(含 {vN} 的原文)整批失配 —— 灵敏度
        实测 vchar +1 字符 → 前 3 页 23 段全 miss(200s)。规范化后键只依赖
        段内结构, 与全局编号解耦。返回 (规范化文本, {原token: 规范token})。"""
        seq = {}

        def _repl(m):
            tok = m.group(0)
            if tok not in seq:
                seq[tok] = "{v%d}" % len(seq)
            return seq[tok]

        return _TOKEN_RE.sub(_repl, text), seq

    @staticmethod
    def _remap(stored_original: str, stored_translation: str, cur_seq: dict) -> str:
        """[v23] 把缓存译文的 {vN} 从存入时编号重映射到当前编号。

        cur_seq: 当前原文的 {当前token: 规范token} 映射。规范→当前反查后
        逐 token 改写; 存入原文中不存在的 token(LLM 自造)原样保留, 与旧行为
        一致。规范键命中意味着两段结构完全同构, 重映射是确定性的。"""
        _, stored_seq = TranslationCache._canon_seq(stored_original)
        canon_to_cur = {canon: tok for tok, canon in cur_seq.items()}

        def _repl(m):
            tok = m.group(0)
            canon_tok = stored_seq.get(tok)
            if canon_tok is None:
                return tok
            return canon_to_cur.get(canon_tok, tok)

        return _TOKEN_RE.sub(_repl, stored_translation)

    # Since peewee and the underlying sqlite are thread-safe,
    # get and set operations don't need locks.
    def get(self, original_text: str, key_suffix: str = "") -> Optional[str]:
        canon_text, cur_seq = self._canon_seq(original_text)
        pj = self._params_json(key_suffix)
        # [v23.4 泛化] 参数历史形态 (去重保序): 当前 → prev(上代, 如 doc fp
        # 加入前) → legacy(更早, 整表 glossary fp)。渲染进程中参数可能演化
        # (摘要 fp 首次加入即触发), 旧行停留在历史形态 —— 每种形态都要能
        # 回查, 否则一次键演化 = 全文重译(实测 601s 教训)。
        forms = [pj]
        for extra in (getattr(self, "prev_params_json", None),
                      getattr(self, "legacy_params_json", None)):
            if not extra:
                continue
            # [v24-A] 历史形态同样要带每段 v23s 后缀: 库中的行以"基形态 +
            # v23s"入库, 快照登记的只是基形态 —— 两种都试, 否则一次键演化
            # (如 doc_summary_fp 加入) 就整篇失配。
            for form in (self._with_suffix(extra, key_suffix), extra):
                if form and form not in forms:
                    forms.append(form)

        def _direct(form, key_text):
            return _TranslationCache.get_or_none(
                translate_engine=self.translate_engine,
                translate_engine_params=form,
                original_text=key_text,
            )

        def _migrate_store(stored_original, stored_translation):
            """[v23.3] 历史形态命中即回写当前形态; 译文规范化必须用
            "存入时原文"的 seq (行内 token 是存入时全局编号)。"""
            try:
                canon_stored, stored_seq_s = self._canon_seq(stored_original)

                def _wb_repl(m):
                    return stored_seq_s.get(m.group(0), m.group(0))

                canon_trans = _TOKEN_RE.sub(_wb_repl, stored_translation)
                _TranslationCache.create(
                    translate_engine=self.translate_engine,
                    translate_engine_params=pj,
                    original_text=canon_stored,
                    translation=canon_trans,
                )
            except Exception as e:
                logger.debug(f"Legacy migration write-back failed: {e}")

        # ① 当前形态: 规范化文本键 → 原文键 (防御)
        row = _direct(pj, canon_text)
        if row is None and canon_text != original_text:
            row = _direct(pj, original_text)
        if row is not None:
            return self._remap(row.original_text, row.translation, cur_seq)
        # ② 历史形态回查: 规范化内存索引 (旧行以 raw 文本入库, sqlite 精确
        # 匹配找不到"规范化相等、原文不等"的行) → 直查 → 命中回写迁移。
        if len(forms) > 1:
            idx = self._legacy_index()
            for form in forms[1:]:
                hit = idx.get((form, canon_text))
                if hit is not None:
                    stored_original, stored_translation = hit
                    # [v24-A] 索引命中同样要回写迁移, 否则下次仍停在旧形态。
                    _migrate_store(stored_original, stored_translation)
                    return self._remap(stored_original, stored_translation, cur_seq)
                row = _direct(form, canon_text)
                if row is None and canon_text != original_text:
                    row = _direct(form, original_text)
                if row is not None:
                    _migrate_store(row.original_text, row.translation)
                    return self._remap(row.original_text, row.translation, cur_seq)
        # ③ [v27] 退役参数投影回查: 润色钩子退场后, 库中"键里带 polish*"的行按
        # 剔除 polish*(及后续退役前缀)后的形态命中, 命中即回写迁移到当前形态
        # (同 ② 约定)。覆盖 ① ② 够不到的全部润色世代 —— 含 polish_guideline_fp
        # 随文档而变、无法用 ladder 重建的那些形态。
        # 门控: 仅当**当前世代本身不含退役参数**时启用。润色若日后重新开启
        # (键里又出现 polish*), 这里直接跳过, 保持世代严格隔离 —— 不会把未润色
        # 的旧译文喂给开启润色的任务。
        # (③ 会剔除 v23s, 所以它的门控必须把 v23s 也算作"退役参数在场"; 带前瞻
        #  段因此走不到 ③, 由 ④ 兜住 —— ④ 不碰 v23s, 见 _reduced_form。)
        if self._strip_retired(pj)[1] == 0:
            idx = self._retired_index()
            for form in forms:
                hit = idx.get((self._strip_retired(form)[0], canon_text))
                if hit is not None:
                    stored_original, stored_translation = hit
                    _migrate_store(stored_original, stored_translation)
                    return self._remap(stored_original, stored_translation, cur_seq)
        # ④ [v28.21] "文档画像"投影回查: 同一段落库里可能躺着多代 doc_summary_fp
        # (配置改动导致摘要键漂移, 见 _DOC_SUMMARY_PARAM), 全部投影到"最小世代
        # 身份"后回查 —— 命中即回写迁移到当前形态(同 ② ③ 约定)。这是把已漂移
        # 的译文救回来、不再整篇重译的那一档。
        # 门控: 只看 polish*(退役世代标记), 不看 v23s。
        # 为什么这里不能沿用 ③ 的门控: v23s 并非世代标记 —— 前瞻(v23.4)至今仍在
        # 生产 `#la:…` 后缀, 用"含 v23s 即关门"会把带前瞻的段全部挡在门外(实测
        # CLAP 每代 13~14 段救不回来)。④ 不剔除 v23s(_reduced_form), 世代隔离由
        # "polish* 仍在形态里"天然保证, 故门控只需挡住"当前任务重新开启润色"。
        if not self._has_retired_generation(pj):
            idx = self._reduced_index()
            for form in forms:
                hit = idx.get((self._reduced_form(form), canon_text))
                if hit is not None:
                    stored_original, stored_translation = hit
                    _migrate_store(stored_original, stored_translation)
                    return self._remap(stored_original, stored_translation, cur_seq)
        return None

    def set(self, original_text: str, translation: str, key_suffix: str = ""):
        try:
            # [v23] 统一以规范化文本入库: 键与全局编号解耦, 译文 token 同步
            # 规范化, 命中时经 _remap 映射回当前编号。
            canon_text, seq = self._canon_seq(original_text)

            def _repl(m):
                return seq.get(m.group(0), m.group(0))

            canon_translation = _TOKEN_RE.sub(_repl, translation)
            _TranslationCache.create(
                translate_engine=self.translate_engine,
                translate_engine_params=self._params_json(key_suffix),
                original_text=canon_text,
                translation=canon_translation,
            )
        except Exception as e:
            logger.debug(f"Error setting cache: {e}")


def init_db(remove_exists=False):
    cache_folder = os.path.join(os.path.expanduser("~"), ".cache", "pdf2zh")
    os.makedirs(cache_folder, exist_ok=True)
    # The current version does not support database migration, so add the version number to the file name.
    cache_db_path = os.path.join(cache_folder, "cache.v1.db")
    if remove_exists and os.path.exists(cache_db_path):
        os.remove(cache_db_path)
    db.init(
        cache_db_path,
        pragmas={
            "journal_mode": "wal",
            "busy_timeout": 1000,
        },
    )
    db.create_tables([_TranslationCache], safe=True)


def init_test_db():
    import tempfile

    cache_db_path = tempfile.mktemp(suffix=".db")
    test_db = SqliteDatabase(
        cache_db_path,
        pragmas={
            "journal_mode": "wal",
            "busy_timeout": 1000,
        },
    )
    test_db.bind([_TranslationCache], bind_refs=False, bind_backrefs=False)
    test_db.connect()
    test_db.create_tables([_TranslationCache], safe=True)
    return test_db


def clean_test_db(test_db):
    test_db.drop_tables([_TranslationCache])
    test_db.close()
    db_path = test_db.database
    if os.path.exists(db_path):
        os.remove(test_db.database)
    wal_path = db_path + "-wal"
    if os.path.exists(wal_path):
        os.remove(wal_path)
    shm_path = db_path + "-shm"
    if os.path.exists(shm_path):
        os.remove(shm_path)


init_db()
