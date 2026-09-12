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
            if extra and extra not in forms:
                forms.append(extra)

        def _direct(form, key_text):
            return _TranslationCache.get_or_none(
                translate_engine=self.translate_engine,
                translate_engine_params=form,
                original_text=key_text,
            )

        def _migrate_store(row):
            """[v23.3] 历史形态命中即回写当前形态; 译文规范化必须用
            "存入时原文"的 seq (行内 token 是存入时全局编号)。"""
            try:
                canon_stored, stored_seq_s = self._canon_seq(row.original_text)

                def _wb_repl(m):
                    return stored_seq_s.get(m.group(0), m.group(0))

                canon_trans = _TOKEN_RE.sub(_wb_repl, row.translation)
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
                    return self._remap(stored_original, stored_translation, cur_seq)
                row = _direct(form, canon_text)
                if row is None and canon_text != original_text:
                    row = _direct(form, original_text)
                if row is not None:
                    _migrate_store(row)
                    return self._remap(row.original_text, row.translation, cur_seq)
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
