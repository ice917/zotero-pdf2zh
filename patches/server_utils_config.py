## server.py v4.0.0
# guaguastandup
# zotero-pdf2zh
import json, toml
import os
import threading
from pathlib import Path

from utils.config_map import (
    pdf2zh_config_map,
    pdf2zh_next_config_map,
    pdf2zh_next_service_aliases,
)
from utils.config_migration import _atomic_write_text, migrate_config_file
from utils.deepseek_thinking import (
    is_deepseek_v4_model,
    normalize_deepseek_extra_data,
    remove_stale_thinking_fields,
    validate_winexe_runtime_if_selected,
)

# [自研补丁 2026-09-03] 并发保护: config.json/config.toml 是所有翻译任务
# 共享的单一文件, Flask threaded=True + 每任务一线程下, 无锁"读-改-写"会
# 交错写坏配置(实测事故: POLISH 键被并发置 null)。进程内全局互斥。
_config_write_lock = threading.Lock()

pdf2zh = 'pdf2zh'
pdf2zh_next = 'pdf2zh_next'

_OPENAI_SEND_TEMPERATURE_ALIAS = 'openai_send_temperature'
_OPENAI_SEND_TEMPERATURE_UPSTREAM = 'openai_send_temprature'


def _normalize_openai_send_temperature_key(values):
    """Map this project's old spelling to pdf2zh_next's compatibility key."""
    if not isinstance(values, dict):
        return
    if (
        _OPENAI_SEND_TEMPERATURE_ALIAS in values
        and _OPENAI_SEND_TEMPERATURE_UPSTREAM not in values
    ):
        values[_OPENAI_SEND_TEMPERATURE_UPSTREAM] = values[
            _OPENAI_SEND_TEMPERATURE_ALIAS
        ]
    values.pop(_OPENAI_SEND_TEMPERATURE_ALIAS, None)


# [自研补丁 2026-09-12] pdf2zh_next 侧强制关闭思考模式的字段表(按服务分组)。
# 字段语义实测自 pdf2zh_next 2.9.0:
#   deepseek_thinking_mode="disabled" -> extra_body={"thinking": {"type": "disabled"}}
#   siliconflow_enable_thinking=False + siliconflow_send_enable_thinking_param=True
#       -> extra_body={"enable_thinking": False}
# 注意 send_*_param=False 的语义是"根本不发这个参数", 服务商会按自己的默认值
# 开启思考; 所以要真正关掉, 必须显式发送 False, 而不是靠不发送。
_NEXT_THINKING_OFF = {
    'deepseek': (
        ('deepseek_thinking_mode', 'disabled'),
        # reasoning_effort 只在 thinking=enabled 时有意义, 一并移除
        ('deepseek_reasoning_effort', None),
    ),
    'siliconflow': (
        ('siliconflow_enable_thinking', False),
        ('siliconflow_send_enable_thinking_param', True),
    ),
}


def _force_disable_next_thinking(service, llm_api):
    """[自研补丁 2026-09-12] 无条件关闭 pdf2zh_next 的思考模式。

    pdf2zh_next 的 LLM 路径要求模型返回结构化 JSON, 思考内容混进响应体有解析
    失败风险, 且会显著增加 token 费用, 故在配置层覆盖插件里保存的设置(总是关闭)。
    只处理当前服务自己的字段, 避免把别家服务的键写进无关的 *_detail 表。
    """
    rules = _NEXT_THINKING_OFF.get(service)
    if not rules:
        return
    extra = llm_api.get('extraData')
    extra = dict(extra) if isinstance(extra, dict) else {}
    changed = []
    for key, value in rules:
        if value is None:
            if extra.pop(key, None) is not None:
                changed.append(f'{key}=已移除')
            continue
        if extra.get(key) != value:
            changed.append(f'{key}={value}')
        extra[key] = value
    llm_api['extraData'] = extra
    if changed:
        print('🧠 [自研补丁] pdf2zh_next 强制关闭思考: ' + ', '.join(changed))


def _safe_log_value(key, value):
    name = str(key or '').lower()
    if any(token in name for token in ('key', 'token', 'secret', 'password', 'auth')):
        raw = str(value or '')
        if not raw:
            return ''
        return ('*' * 8 + raw[-4:]) if len(raw) > 4 else ('*' * len(raw))
    return value


_NEXT_GLOSSARY_HEADER = ('source', 'target')


def _build_next_glossary():
    """[自研补丁 2026-09-04] 为 pdf2zh_next 派生一份带表头的术语表副本。

    babeldoc 的 Glossary.from_csv 用 csv.DictReader 解析, 并强制要求存在
    source/target 两列; 本项目 glossary/terms.csv 是无表头的两列文件,
    直接透传会抛 ValueError, 整篇翻译在配置装载阶段就失败。故派生一份
    副本, 源文件保持不动(pdf2zh 1.x 的润色管线仍读源文件)。

    返回可用的绝对路径字符串; 无术语表或派生失败时返回 None。
    """
    src = Path(__file__).resolve().parent.parent / 'glossary' / 'terms.csv'
    if not src.is_file():
        return None
    try:
        import csv
        import io as _io
        with src.open('r', encoding='utf-8-sig', newline='') as f:
            rows = [r for r in csv.reader(f) if r and any(c.strip() for c in r)]
        # 用户手工编辑时可能补上表头, 避免写出重复表头行
        if rows and [c.strip().lower() for c in rows[0][:2]] == list(
                _NEXT_GLOSSARY_HEADER):
            body = rows
        else:
            body = [list(_NEXT_GLOSSARY_HEADER)] + rows
        buf = _io.StringIO()
        csv.writer(buf).writerows(body)
        _atomic_write_text(src.with_name('terms.babeldoc.csv'), buf.getvalue())
    except Exception as exc:
        print(f"⚠️ 派生 pdf2zh_next 术语表失败, 本次不透传术语表: {exc}")
        return None
    return str(src.with_name('terms.babeldoc.csv'))


def stringToBoolean(value):
    if value == 'true' or value == 'True' or value == True or value == 1:
        return True
    return False


_TRUE_VALUES = {'true', '1', 'yes', 'on'}
_FALSE_VALUES = {'false', '0', 'no', 'off'}


def resolve_pdf2zh_next_service(service):
    return pdf2zh_next_service_aliases.get(service, service)


def _is_pdf2zh_next_bool_extra_key(key):
    name = str(key or '')
    return (
        name.endswith('_enable_json_mode')
        or name.endswith('_send_temprature')
        or name.endswith('_send_temperature')
        or name.endswith('_send_reasoning_effort')
        or name.endswith('_enable_thinking')
        or name.endswith('_send_enable_thinking_param')
    )


def coerce_pdf2zh_next_extra_value(key, value):
    """Keep extraData types compatible with pdf2zh_next pydantic fields.

    The plugin always sends extraData values as strings. toml needs native
    bool/int so fields like openai_enable_json_mode=false actually work.
    Empty values are skipped by the caller; False and 0 must still be written.
    """
    if isinstance(value, bool):
        return value
    if _is_pdf2zh_next_bool_extra_key(key):
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        text = str(value).strip().lower()
        if text in _TRUE_VALUES:
            return True
        if text in _FALSE_VALUES:
            return False
        return value
    if str(key) == 'num_predict':
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return value
    return value


class Config:
    def __init__(self, request_data):
        self.engine = request_data.get('engine', 'pdf2zh')
        if self.engine not in [pdf2zh, pdf2zh_next]:
            self.engine = pdf2zh

        if self.engine == pdf2zh:
            # [v23] 缺省回落守卫: bing 静默回落曾致整文档换键重译(实测 341 段
            # 4.5 分钟), 直提请求必须带全插件同等字段。回落时大声告警。
            if 'service' not in request_data or not request_data.get('service'):
                print("⚠️ [Config] 请求未带 service 字段, 回落缺省 'bing'!"
                      " 直提 /translate 请带全插件同等配置(service=silicon/targetLang=zh-CN 等)")
            self.service = request_data.get('service', 'bing')
            if self.service in [None, ''] or len(self.service) < 3:
                self.service = 'bing'
        else:
            if not request_data.get('next_service') or request_data.get('next_service') in [None, '']:
                self.service = request_data.get('service', 'siliconflowfree')
            else:
                self.service = request_data.get('next_service', 'siliconflowfree')
            if self.service in [None, ''] or len(self.service) < 3:
                self.service = 'siliconflowfree'

        self.sourceLang = request_data.get('sourceLang', 'en')
        if self.sourceLang in [None, ''] or len(self.sourceLang) < 2:
            self.sourceLang = 'en'
        self.targetLang = request_data.get('targetLang', 'zh-CN')
        if self.targetLang in [None, ''] or len(self.targetLang) < 2:
            self.targetLang = 'zh-CN'

        self.skip_last_pages = request_data.get('skipLastPages', 0)
        try:
            self.skip_last_pages = int(self.skip_last_pages)
        except (ValueError, TypeError):
            self.skip_last_pages = 0
        if self.skip_last_pages < 0:
            self.skip_last_pages = 0

        self.thread_num = request_data.get('threadNum', 8)
        try: 
            self.thread_num = int(self.thread_num)
            if self.thread_num < 1:
                self.thread_num = 8
        except (ValueError, TypeError):
            self.thread_num = 8
        
        self.qps = request_data.get('qps', 4)
        try:
            self.qps = int(self.qps)
        except (ValueError, TypeError):
            self.qps = 4
        if self.qps < 1:
            self.qps = 4
        
        self.pool_size = request_data.get('poolSize', 0)
        try:
            self.pool_size = int(self.pool_size)
        except (ValueError, TypeError):
            self.pool_size = 0

        # pdf2zh_next uses qps as the worker count when pool_max_workers is unset.
        # Keep 0 as "unset/follow qps" instead of the legacy qps * 10 expansion.
        if self.pool_size < 0:
            self.pool_size = 0
        if self.pool_size > 1000:
            self.pool_size = 1000

        # 如果左右留白部分裁剪太多了, 可以调整pdf_w_offset和pdf_offset_ratio, 宽边裁剪值pdf_w_offset, 窄边裁剪值pdf_w_offset/pdf_offset_ratio
        # TODO: 将裁剪的逻辑添加到zotero配置页面
        # [自研补丁 2026-09-03] 裁剪参数容错: 非数字输入回退默认值,
        # 避免插件传入异常值时整个请求 500 (与 threadNum 同款守卫)
        self.pdf_w_offset = self._int_opt(request_data, 'pdf_w_offset', 40)
        self.pdf_h_offset = self._int_opt(request_data, 'pdf_h_offset', 20)
        self.pdf_offset_ratio = self._float_opt(request_data, 'pdf_offset_ratio', 5.0)
        self.pdf_white_margin = self._int_opt(request_data, 'pdf_white_margin', 0)

        self.mono = stringToBoolean(request_data.get('mono', True))
        self.dual = stringToBoolean(request_data.get('dual', True))
        self.mono_cut = stringToBoolean(request_data.get('mono_cut', False))
        self.dual_cut = stringToBoolean(request_data.get('dual_cut', False))
        self.crop_compare = stringToBoolean(request_data.get('crop_compare', False))
        self.compare = stringToBoolean(request_data.get('compare', False))
        # pdf2zh 1.x
        self.babeldoc = stringToBoolean(request_data.get('babeldoc', False))
        self.skip_font_subsets = stringToBoolean(request_data.get('skipSubsetFonts', False))
        self.font_file = request_data.get('fontFile', '') # pdf2zh 对应的字体路径
        # pdf2zh 2.x
        self.font_family = request_data.get('fontFamily', 'auto') # pdf2zh_next对应的字体选择
        self.dual_mode = request_data.get('dualMode', 'LR')
        self.trans_first = stringToBoolean(request_data.get('transFirst', False))
        self.ocr = stringToBoolean(request_data.get('ocr', False))
        self.auto_ocr = stringToBoolean(request_data.get('autoOcr', False))
        self.no_watermark = stringToBoolean(request_data.get('noWatermark', True))
        self.save_auto_extracted_glossary = stringToBoolean(request_data.get('saveGlossary', False))
        self.disable_glossary = stringToBoolean(request_data.get('disableGlossary', False))
        self.no_dual = stringToBoolean(request_data.get('noDual', False))
        self.no_mono = stringToBoolean(request_data.get('noMono', False))
        self.skip_clean = stringToBoolean(request_data.get('skipClean', False))
        self.enhance_compatibility = stringToBoolean(request_data.get('enhanceCompatibility', False))
        self.disable_rich_text_translate = stringToBoolean(request_data.get('disableRichTextTranslate', False))
        self.translate_table_text = stringToBoolean(request_data.get('translateTableText', False))
        self.only_include_translated_page = stringToBoolean(request_data.get('onlyIncludeTranslatedPage', False))

        # [自研补丁 2026-09-03] 删除遗留调试 print(self.__dict__):
        # 该输出含全部请求配置, 未来新增密钥类字段时有泄密风险。

        self.llm_api = {
            'apiKey': request_data.get('llm_api', {}).get('apiKey', ''),
            'apiUrl': request_data.get('llm_api', {}).get('apiUrl', ''),
            'model': request_data.get('llm_api', {}).get('model', ''),
            'threadnum': request_data.get('llm_api', {}).get('threadNum', self.thread_num), # TODO, 为每个服务单独配置线程数, 暂时不实现
            'extraData': request_data.get('llm_api', {}).get('extraData', {})
        }

    @staticmethod
    def _int_opt(data, key, default):
        """[自研补丁] 整数参数容错: 非法/缺失回退默认, 不抛 500。"""
        try:
            return int(data.get(key, default))
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _float_opt(data, key, default):
        """[自研补丁] 浮点参数容错, 同 _int_opt。"""
        try:
            return float(data.get(key, default))
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _load_config_with_heal(config_file, kind):
        """[自研补丁 2026-09-03] 运行期配置自愈: 配置迁移只在启动时执行,
        若运行中 config.json/toml 损坏(半截写/手工改坏), 这里兜底再迁移一次
        (迁移逻辑会备份坏文件并恢复默认), 仍失败则抛业务错误而非裸 500。"""
        try:
            if kind == 'json':
                with open(config_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            with open(config_file, 'r', encoding='utf-8') as f:
                return toml.load(f)
        except (ValueError, OSError) as exc:
            print(f"⚠️ 配置文件读取失败({exc}), 尝试迁移自愈: {config_file}")
            try:
                migrate_config_file(config_file)
                if kind == 'json':
                    with open(config_file, 'r', encoding='utf-8') as f:
                        return json.load(f)
                with open(config_file, 'r', encoding='utf-8') as f:
                    return toml.load(f)
            except Exception as exc2:
                raise RuntimeError(
                    f"配置文件 {config_file} 无法读取且自愈失败: {exc2}") from exc2

    def update_config_file(self, config_file):
        """[自研补丁 2026-09-03] 并发安全包装: 读-改-写全程持全局锁,
        防止并发任务的配置互相覆盖/交错写坏。"""
        with _config_write_lock:
            return self._update_config_file_locked(config_file)

    def _update_config_file_locked(self, config_file):
        service = self.service
        engine = self.engine
        if engine == pdf2zh:
            # 更新llm api config
            config_map = pdf2zh_config_map.get(service, {})

            # [自研补丁 2026-09-03] 运行期自愈加载(替换裸 json.load)
            old_config = self._load_config_with_heal(config_file, 'json')
            new_config = old_config.copy()

            # [自研补丁 2026-09-03] 字体路径是全局键(NOTO_FONT_PATH), 与具体
            # 翻译服务无关: 必须在 config_map 判空之前写入并落盘, 否则
            # bing/google 等无 config_map 的服务会让字体配置被静默忽略。
            font_updated = False
            if os.path.exists(self.font_file):
                new_config['NOTO_FONT_PATH'] = self.font_file
                font_updated = True
                print(f"✏️ 更新字体路径: {self.font_file}")

            if not config_map: # 无需映射, 直接跳过
                print(f"🔍 No config_map found for service: {service}, 如果是新的服务, 请联系开发者更新config_map, 如果不是请忽略")
                if font_updated:
                    _atomic_write_text(
                        Path(config_file),
                        json.dumps(new_config, indent=4, ensure_ascii=False))
                    print(f"✏️ 更新 config file(仅字体): {config_file}")
                return

            # 我们假设config.json文件的格式没有问题
            translator = None
            for t in new_config['translators']:
                if t.get('name') == service:
                    translator = t
                    break
            
            if translator is None:
                print(f"✏️ 服务 '{service}' 在先前配置中不存在, 创建新配置")
                translator = {'name': service, 'envs': {}}
                new_config['translators'].append(translator)
            else:
                if not isinstance(translator.get('envs'), dict): 
                    translator['envs'] = {}

            translator_keys = []
            if 'extraData' in config_map:
                for key in config_map['extraData']:
                    translator_keys.append(key)

            # 先对三个基本的参数进行映射, 如果存在映射关系, 则更新
            keys = ['apiKey', 'apiUrl', 'model'] 
            for key in keys:
                if key in self.llm_api and key in config_map:
                    value = self.llm_api[key]
                    mapped_key = config_map[key]
                    if value not in (None, "", [], {}):  # 跳过空值
                        translator['envs'][mapped_key] = value
                        translator_keys.append(mapped_key)
                        if key == "apiKey":
                            # [自研补丁] 掩码复用 _safe_log_value, 非字符串值不再 len() 崩溃
                            print(f"✏️ 更新 {key}: {mapped_key} = {_safe_log_value('apiKey', value)}")
                        else:
                            print(f"✏️ 更新 {key}: {mapped_key} = {value}") 
                    else:
                        # [自研补丁] 空值时不覆盖已有值, 但必须加入保留列表;
                        # 否则会被下方"删除不在保留列表中的 key"循环清掉, 配置照样丢失
                        translator_keys.append(mapped_key)
                        print(f"✏️ 跳过 {key}: {mapped_key} = {value} (empty or null)")

            # 将用户设置的extraData也进行映射, 如果存在映射关系, 则更新
            if 'extraData' in self.llm_api and isinstance(self.llm_api['extraData'], dict):
                for key, value in self.llm_api['extraData'].items():
                    if value not in (None, "", [], {}):
                        translator['envs'][key] = value
                        translator_keys.append(key)
                        print(f"✏️ 更新 extraData: {key} = {_safe_log_value(key, value)}")
                    else:
                        # [自研补丁] 同上: 空值不覆盖, 但仍需加入保留列表防止被删除循环清掉
                        translator_keys.append(key)
                        print(f"✏️ 跳过 extraData: {key} = {value} (empty or null)")

            # [自研补丁] 保护润色钩子配置: POLISH_* 键不被插件请求刷新删除,
            # 从 config.json.example 托管默认读取 (config.json 会被插件推送/迁移反复覆写,
            # POLISH 键可能被置 null, 以 example 为稳定事实源) (2026-09-02 改)
            # 注意: 这是有意托管——POLISH_* 以 example 为准无条件回填,
            # 插件 UI 对这些键的修改不会生效; 调整润色配置请改 config.json.example。
            try:
                _server_cfg_path = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    'config', 'config.json.example')
                with open(_server_cfg_path, 'r', encoding='utf-8') as f:
                    _server_cfg = json.load(f)
                for _t_src in _server_cfg.get('translators', []):
                    if _t_src.get('name') == service:
                        for _k, _v in (_t_src.get('envs') or {}).items():
                            if _k.startswith('POLISH') and _v not in (None, "", [], {}):
                                translator['envs'][_k] = _v
                                translator_keys.append(_k)
                                print(f"✏️ 润色钩子配置: {_k} = {_v}")
                        break
            except Exception:
                print("⚠️ 读取润色钩子预置配置失败, POLISH 键可能丢失")

            # 将所有不在translator_keys中的key删除
            for key in list(translator['envs']):
                if key not in translator_keys:
                    del translator['envs'][key]
                    print(f"✏️ 删除旧 {key}")

            # [自研补丁 2026-09-03] 原子写: 防止并发子进程读到半截 JSON
            _atomic_write_text(
                Path(config_file),
                json.dumps(new_config, indent=4, ensure_ascii=False))
            print(f"✏️ 更新 config file: {config_file}")
            
        elif engine == pdf2zh_next: # toml文件, 格式参考server/config/config.toml.example
            service = resolve_pdf2zh_next_service(service)
            config_map = pdf2zh_next_config_map.get(service, {})
            if not config_map:
                print(f"✏️ No config_map found for service: {service}, 如果是新的服务, 请联系开发者更新config_map")
                return

            # [自研补丁 2026-09-03] 运行期自愈加载(替换裸 toml.load)
            old_config = self._load_config_with_heal(config_file, 'toml')

            # A previous DeepSeek V4 attempt may have written 2.9-only fields.
            # For every non-DeepSeek request, scrub those fields before an older
            # runtime gets a chance to parse the shared config.toml.
            if service != 'deepseek':
                old_deepseek_detail = old_config.get('deepseek_detail')
                if isinstance(old_deepseek_detail, dict):
                    remove_stale_thinking_fields(old_deepseek_detail, '')

            # extraData is the generic plugin transport. DeepSeek V4 uses it to
            # carry the user's intent, but the server normalizes the values here
            # and execute.py later maps them to the exact upstream CLI flags.
            effective_deepseek_model = ''
            if service == 'deepseek':
                effective_deepseek_model = normalize_deepseek_extra_data(
                    self.llm_api,
                    old_config,
                )
            # [自研补丁 2026-09-12] 强制关闭思考, 必须早于下面的 winexe 运行时校验:
            # 否则插件里残留的 "enabled" 会先触发校验, 把整次翻译拦下来。
            _force_disable_next_thinking(service, self.llm_api)
            if service == 'deepseek' and is_deepseek_v4_model(
                    effective_deepseek_model):
                extra_data = self.llm_api.get("extraData") or {}
                thinking_mode = extra_data.get(
                    "deepseek_thinking_mode", "disabled"
                )
                # winexe bypasses execute_with_progress(), so protect that
                # execution path here. uv/conda/system runtimes are checked
                # against the exact executable immediately before launch.
                write_thinking_fields = validate_winexe_runtime_if_selected(
                    config_file,
                    effective_deepseek_model,
                    thinking_mode=thinking_mode,
                )
                if not write_thinking_fields:
                    extra_data.pop("deepseek_thinking_mode", None)
                    extra_data.pop("deepseek_reasoning_effort", None)
                    self.llm_api["extraData"] = extra_data

            new_config = old_config.copy() # 我们假设config.toml文件的格式没有问题

            # Keep request-scoped pdf2zh_next options in config.toml so they work
            # even when there is no dedicated CLI wiring in server.py.
            translation_config = new_config.setdefault('translation', {})
            # [自研补丁 2026-09-03] TOML 没有 null 类型: 旧写法写入字符串 "null",
            # 任何新消费者 int() 即崩。不写该键即表示"未设置", pdf2zh_next
            # 会回退默认(worker 数跟随 qps)。
            if self.pool_size > 0:
                translation_config['pool_max_workers'] = self.pool_size
            else:
                translation_config.pop('pool_max_workers', None)
            pdf_config = new_config.setdefault('pdf', {})
            pdf_config['only_include_translated_page'] = self.only_include_translated_page
            # [自研补丁 2026-09-04] 表格文字开关必须以 config.toml 为准:
            # pdf2zh_next 的 PDFSettings.translate_table_text 模型默认值是 True,
            # build_args_parser 按默认值把它注册成 action="store_false", 于是
            # CLI 传 --translate-table-text 反而会把它关掉(上游语义反转 bug),
            # 实测 settings 里恒为 False。故不再走 CLI, 改在此写入权威值。
            pdf_config['translate_table_text'] = self.translate_table_text

            # [自研补丁 2026-09-04] 术语表透传(此前是 server.py 里的 TODO)。
            # 无术语表时必须 pop, 否则会残留上一次请求的路径。
            _next_glossary = _build_next_glossary()
            if _next_glossary:
                translation_config['glossaries'] = _next_glossary
                print(f"✏️ 透传术语表: {_next_glossary}")
            else:
                translation_config.pop('glossaries', None)
            # 有自有术语表时关掉自动抽取: 避免两套术语源互相打架, 同时省掉
            # 全文档术语抽取那一整轮 LLM 调用(实测 34 页 4034 项)。
            translation_config['no_auto_extract_glossary'] = (
                bool(_next_glossary) or self.disable_glossary)

            translator = None 
            if f'{service}_detail' in new_config:
                translator = new_config[f'{service}_detail']
            else:
                print(f"✏️ 服务 '{service}' 在先前配置中不存在, 创建新配置")
                translator = {}
                new_config[f'{service}_detail'] = translator

            if service == 'deepseek':
                remove_stale_thinking_fields(translator, effective_deepseek_model)
            elif service == 'openai':
                # Older zotero-pdf2zh releases exposed the correctly spelled
                # key, while pdf2zh_next intentionally retains the historical
                # "temprature" typo. Migrate both saved TOML and persisted
                # plugin extraData without affecting OpenAI-compatible services.
                _normalize_openai_send_temperature_key(translator)
                _normalize_openai_send_temperature_key(
                    self.llm_api.get('extraData')
                )
            
            translator_keys = ['translate_engine_type', 'support_llm']
            if 'extraData' in config_map:
                for key in config_map['extraData']:
                    translator_keys.append(key)

            keys = ['apiKey', 'apiUrl', 'model']
            for key in keys:
                if key in self.llm_api and key in config_map:
                    value = self.llm_api[key]
                    mapped_key = config_map[key]
                    if value not in (None, "", [], {}):
                        translator[mapped_key] = value
                        translator_keys.append(mapped_key)
                        if key == "apiKey":
                            # [自研补丁] 掩码复用 _safe_log_value, 非字符串值安全
                            print(f"✏️ 更新 {key}: {mapped_key} = {_safe_log_value('apiKey', value)}")
                        else:
                            print(f"✏️ 更新 {key}: {mapped_key} = {value}")
                    else:
                        translator_keys.append(mapped_key)
                        print(f"✏️ 跳过 {key}: {mapped_key} = {value} (empty or null)")
            
            # 将用户设置的extraData也进行映射, 如果存在映射关系, 则更新
            if 'extraData' in self.llm_api and isinstance(self.llm_api['extraData'], dict):
                for key, value in self.llm_api['extraData'].items():
                    coerced = coerce_pdf2zh_next_extra_value(key, value)
                    if coerced not in (None, "", [], {}):
                        translator[key] = coerced
                        translator_keys.append(key)
                        print(f"✏️ 更新 extraData: {key} = {_safe_log_value(key, coerced)}")
                    else:
                        # [自研补丁] 同上: 空值不覆盖, 但仍需加入保留列表防止被删除循环清掉
                        translator_keys.append(key)
                        print(f"✏️ 跳过 extraData: {key} = {value} (empty or null)")

            # 将translator中, 所有不在translator_keys中的key删除
            # [自研补丁 2026-09-03] 删除遗留调试 print(translator.keys())
            for key in list(translator.keys()):
                if key not in translator_keys: 
                    del translator[key]
                    print(f"✏️ 删除旧 {key}")

            # [自研补丁 2026-09-03] 原子写(同 JSON 分支)
            _atomic_write_text(Path(config_file), toml.dumps(new_config))
            print(f"✏️ 更新 config file: {config_file}")

            # server.py in older releases uses a legacy singular pool flag.
            # The worker count is already persisted above, so suppress that CLI path.
            self.pool_size = 0
        else:
            print(f"✏️ 不支持的引擎类型: {engine}")
