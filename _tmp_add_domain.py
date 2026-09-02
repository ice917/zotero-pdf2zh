# -*- coding: utf-8 -*-
"""临时脚本: 给 silicon 服务的 envs 添加 POLISH_DOMAIN (验证后删除)"""
import json
import io
import shutil

CFG = r'D:\zotero-pdf2zh\server\config\config.json'

# 备份
shutil.copyfile(CFG, CFG + '.bak-domain')

cfg = json.load(io.open(CFG, encoding='utf-8'))

changed = []
for tr in cfg.get('translators', []):
    if tr.get('name') == 'silicon':
        envs = tr.setdefault('envs', {})
        # 插在 POLISH_GLOSSARY 附近, 保持可读性
        envs['POLISH_DOMAIN'] = 'topology optimization / soft robotics'
        changed.append(tr['name'])

# 写回(UTF-8 无 BOM, ensure_ascii=False 保持中文可读)
with io.open(CFG, 'w', encoding='utf-8') as f:
    json.dump(cfg, f, ensure_ascii=False, indent=4)

print('modified translators:', changed)
# 验证
cfg2 = json.load(io.open(CFG, encoding='utf-8'))
for tr in cfg2.get('translators', []):
    if tr.get('name') == 'silicon':
        print('POLISH_DOMAIN =', tr['envs'].get('POLISH_DOMAIN'))
        print('POLISH_GLOSSARY =', tr['envs'].get('POLISH_GLOSSARY'))
