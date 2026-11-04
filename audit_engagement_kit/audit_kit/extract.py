# -*- coding: utf-8 -*-
"""extract.py — 审计机构聘任公告「(二)项目信息」抽取引擎 v2

设计要点
--------
1. 版面重建：以 words 坐标重建「视觉行 / 单元格」，解决多列表格被 PDF 文本流
   拆散的问题（如「邵建\n克」实为「邵建克」）。
2. 四种版式分别处理：
   R  行式表格：项目合伙人 | 郑飞 | 2012年 ...
   T  田字表格（表头为角色）：项目合伙人 | 签字注册会计师 | 复核人
                            姓名       | 唐红雨          | 郭成      | 刘志坚
   P  叙述式：项目合伙人：张三先生，2015 年成为注册会计师……
   C  合称式：项目合伙人及签字注册会计师 1：王璟，……
3. 姓名合法性：姓氏白名单 + 停用词 + 上市公司简称黑名单 + 否定式上下文排除
   （「近三年未因执业行为受到刑事处罚」等不能当姓名）。
"""
from __future__ import annotations

import re
import pymupdf

from . import times as T

# ---------------------------------------------------------------- 角色
ROLE_P = '项目合伙人'
ROLE_S = '签字注册会计师'
ROLE_R = '质量控制复核人'
ROLES = (ROLE_P, ROLE_S, ROLE_R)

LABEL_PAT = {
    ROLE_P: r'项\s*目\s*合\s*伙\s*人',
    ROLE_S: r'(?:第\s*[一二三]\s*)?(?:拟\s*)?(?:签\s*字\s*注\s*册\s*会\s*计\s*师|签\s*字\s*会\s*计\s*师)',
    ROLE_R: (r'(?:审\s*计\s*)?(?:项\s*目\s*)?(?:独\s*立\s*)?(?:质\s*量\s*)?(?:控\s*制\s*)?复\s*核'
             r'(?:\s*人\s*员|\s*负\s*责\s*人|\s*合\s*伙\s*人|\s*人)'
             r'|(?:审\s*计\s*)?(?:项\s*目\s*)?(?:独\s*立\s*)?(?:质\s*量\s*)?(?:控\s*制\s*)?复\s*核'
             r'(?:\s*合\s*伙\s*人)'
             r'|(?:审\s*计\s*)?(?:项\s*目\s*)?(?:独\s*立\s*)?质\s*量\s*控\s*制\s*负\s*责\s*人'
             r'|(?:审\s*计\s*)?(?:项\s*目\s*)?质\s*量\s*复\s*核\s*负\s*责\s*人'),
}
LABEL_RE = {k: re.compile(v) for k, v in LABEL_PAT.items()}

# 姓名之后的合法字符：标点/空白/数字/结束，或常见动词与介词开头
BOUNDARY = (
    r'(?=(?:先生|女士)?[，,。；;：:、\s（）()]|\d|$|'
    r'自|于|在|从|进|加|现|长|曾|已|开|起|主|担|为|持|拥|具|获|'
    r'参|负|任|及|和|与|并|而|至|到|连|多|累|先|后|分|赴|驻)'
)
# 用于判断「某单元格是否是角色标签的开头」
LABEL_PREFIX = re.compile(
    r'^(?:项|项目|项目合|项目合伙|签|签字|签字注|签字注册|签字注册会|签字注册会计|'
    r'质|质量|质量控|质量控制|质量控制复|质量控制复核|控|控制|控制复|控制复核|复|复核|独立)$')

NEG_AFTER = re.compile(
    r'^(?:近\s*三年|最近\s*三年|最近\s*三个|不\s*存在|未\s*因|未\s*受|无\s*因|'
    r'不\s*违\s*反|均具|均不|均未)')

# ---------------------------------------------------------------- 姓名合法性
SURNAME1 = set(
    '王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘于蒋蔡余杜叶程苏魏吕丁任'
    '沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔'
    '向汤常温康施文牛樊葛邢安齐易乔伍庞颜倪庄聂章鲁岳翟殷詹申欧耿关兰焦俞左柳甘祝包宁尚符舒阮柯纪梅童凌'
    '毕单季裴霍涂成苗谷盛曲翁冉骆蓝路游辛靳管柴蒙鲍华喻祁蒲房滕屈饶解牟艾尤阳时穆农司卓古吉缪简车项连芦'
    '麦褚娄窦戚岑景党宫费卜冷晏席卫米柏宗瞿桂全佟应臧闵苟邬边卞姬师和仇栾隋商刁沙荣巫寇桑郎甄丛仲虞敖巩'
    '明佘池查麻苑迟邝封谈匡鞠惠荆乐冀郁胥能班储栗燕楚鄢劳谌奚皮粟冼蔺楼闻厉伊衡富尧竹国太'
    # —— 二轮补充（从语料首字频次挖掘出的真实姓氏）——
    '洪傅赖弋代强权树闾禹索尉支占宣戎凡嵇沃揭蒯狄银宛屠修晁束莘昝诸巢公区平阚茅况盖扶琚步綦裘密淦皇逯'
    '海朴轩由赛都阎原花茆么宿弓慕祖鹿之戈旷杭母郗钦威宏永添福腾云兴印宜建恒世信冒呼唱奥岗广泮濮税门鱼'
    '丽亚光卯呙宾居帅干战扈拱来纳航韶毋亓仉仝邰郜鄢乜缑亢逄漏郏亓汲禤缶鄂降报赫冶阿修党完令狐贝卿东昌靖芷蹇'
)
SURNAME2 = set('欧阳 司马 上官 诸葛 东方 皇甫 尉迟 公孙 慕容 长孙 宇文 司徒 鲜于 独孤 南宫 令狐 夏侯 端木 西门 百里 呼延'.split())

BAD_WORDS = set('''
时间 家次 超过 经验 简历 计师 家上 年度 情况 信息 姓名 人员 项目 合伙人 会计师 事务所
中国 注册 会计 签字 复核 质量 控制 本次 目前 最近 三年 上市 公司 股份 集团 审计 报告
服务 从业 执业 具备 相应 上述 如下 详见 其中 同时 从事 变更 发生 不变 开始 成员 职业
是否 性质 身份 证件 号码 住址 学历 职务 简介 资格 具有 以上 相关 方面 业务 收入 客户
行业 制造 交通 运输 批发 零售 房地 金融 建筑 电力 热力 燃气 供水 传输 软件 信息
技术 科学 研究 文化 传播 综合 挂牌 名称 基本 独立 诚信 记录 报酬 收费 万元 年度
审计费 审计收费 事务所 会计师 注册会计 上市公司 本公司 服务年限 项目组 联合 类 某些
能力 胜任 资质 专业 水平 方面 相关 经验丰富 良好 情况 说明 内容 披露 报告期
''' .split())

_STOP_CHARS = set('及和与为是等的该其本现自于在已将拟副总另各每我司年均具备')
_STOP_WORDS = ('中国', '注册', '会计', '事务', '基本', '信息', '项目', '合伙', '签字',
               '复核', '质量', '控制', '人员', '先生', '女士', '本所', '公司', '审计',
               '报告', '证券', '从业', '执业', '具备', '相应', '上述', '如下', '详见',
               '其中', '同时', '从事', '变更', '发生', '不变', '开始', '姓名', '成员',
               '职业', '是否', '性质', '身份', '证件', '号码', '住址', '学历', '职务',
               '简介', '年度', '本次', '资格', '具有', '以上', '相关', '情况', '最近',
               '三年', '上市', '服务', '业务', '工作', '股份', '集团', '银行', '控股',
               '科技', '实业', '受到', '任何', '行政', '处罚', '刑事', '监管', '措施',
               '纪律', '处分', '形式', '规定', '违反', '不存在', '遵守', '要求', '经验',
               '时间', '家次', '超过', '简历', '计师', '家上')


def is_cjk(s):
    return bool(re.fullmatch(r'[\u4e00-\u9fff]+', s or ''))


_co_names = set()


def set_co_names(names):
    global _co_names
    _co_names = {n for n in names if n}


def valid_name(s):
    if not s or not (2 <= len(s) <= 4) or not is_cjk(s):
        return False
    if s in BAD_WORDS or s in _co_names:
        return False
    if TAIL_NOISE.search(s):
        return False
    if any(w in s for w in _STOP_WORDS):
        return False
    if any(ch in s for ch in _STOP_CHARS):
        return False
    if s[:2] in SURNAME2:
        return True
    return s[0] in SURNAME1


# ---------------------------------------------------------------- 版面重建
def page_rows(page, tol_ratio=0.55):
    """words -> 视觉行 [{'y','cells':[(x0,x1,txt)],'h'}]"""
    try:
        words = page.get_text('words')
    except Exception:
        return []
    if not words:
        return []
    hs = sorted(w[3] - w[1] for w in words)
    h = hs[len(hs) // 2]
    h = max(h, 5.0)
    tol = max(2.5, h * tol_ratio)

    ws = sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0]))
    groups = []
    for w in ws:
        yc = (w[1] + w[3]) / 2
        if groups and abs(yc - groups[-1]['y']) <= tol:
            g = groups[-1]
            g['y'] = (g['y'] * len(g['ws']) + yc) / (len(g['ws']) + 1)
            g['ws'].append(w)
        else:
            groups.append({'y': yc, 'ws': [w]})

    rows = []
    for g in groups:
        g['ws'].sort(key=lambda w: w[0])
        cells, cur = [], None
        for w in g['ws']:
            if cur is not None and (w[0] - cur[1]) <= max(6.0, h * 0.9):
                cur[1] = max(cur[1], w[2])
                cur[2] += w[4]
            else:
                if cur:
                    cells.append(tuple(cur))
                cur = [w[0], w[2], w[4]]
        if cur:
            cells.append(tuple(cur))
        rows.append({'y': g['y'], 'cells': cells, 'h': h})
    return rows


def row_text(row, sep=' '):
    return sep.join(c[2] for c in row['cells'])


def rows_text(rows):
    return '\n'.join(row_text(r) for r in rows)


# ---------------------------------------------------------------- 角色定位
def _cell_join_below(rows, i, j, h):
    """把跨行拆开的角色标签拼起来：返回 (role, end_row_idx, end_cell_idx) 或 None

    兼容「审计项目合 / 伙人」「质量控制复 / 核人」「拟签字注册 / 会计师」这类
    窄列换行写法。
    """
    t = re.sub(r'\s+', '', rows[i]['cells'][j][2])
    if len(t) < 2:
        return None
    x0, x1, _ = rows[i]['cells'][j]
    for k in (i + 1, i + 2):
        if k >= len(rows) or rows[k]['y'] - rows[i]['y'] > h * 2.6:
            continue
        for j2c, (bx0, bx1, btxt) in enumerate(rows[k]['cells']):
            if abs(bx0 - x0) > max(10.0, h * 0.9):
                continue
            joined = t + re.sub(r'\s+', '', btxt)
            for role, rx in LABEL_RE.items():
                m = rx.search(joined)
                if m and m.end() > len(t) - 1 and m.start() <= 10:
                    return (role, k, j2c)
    return None


def _find_role_cells(rows):
    """-> [{'ri','ci','role','x0','x1','y0','y1','text'}]"""
    out, seen = [], set()
    for i, row in enumerate(rows):
        h = row['h']
        for j, (x0, x1, txt) in enumerate(row['cells']):
            t = re.sub(r'\s+', '', txt)
            role, end_i = None, i
            for r, rx in LABEL_RE.items():
                m = rx.search(t)
                if m and m.start() <= 10 and m.end() >= len(t) - 2:
                    role = r
                    break
            if role is None:
                jr = _cell_join_below(rows, i, j, h)
                if jr:
                    role, end_i, j2 = jr
                    x1 = max(x1, rows[end_i]['cells'][j2][1])
            if role and (i, j, role) not in seen:
                seen.add((i, j, role))
                out.append({'ri': i, 'ci': j, 'role': role, 'x0': x0, 'x1': x1,
                            'y0': row['y'], 'y1': rows[end_i]['y'], 'text': t})
    return out


# ---------------------------------------------------------------- 姓名取值
TAIL_NOISE = re.compile(
    r'(?:近三年|最近三年|近三|近两|最近|三年|两年|时间|先生|女士|年度|负责|担任|成为'
    r'|简历|情况|信息)$')


def clean_name_tail(s):
    """去掉字符串尾部粘上的非姓名词语（如「程峰近三」→「程峰」）"""
    if not s:
        return s
    for _ in range(3):
        m = TAIL_NOISE.search(s)
        if not m or m.start() < 2:
            break
        s = s[:m.start()]
    return s


def _name_before_label(rows, ri, ci, role):
    """表格里「姓名 | 职务」列式，或同一单元格内「姓名+职务」连写 -> 姓名"""
    row = rows[ri]
    x0, x1, txt = row['cells'][ci]
    t = re.sub(r'\s+', '', txt)
    m = LABEL_RE[role].search(t)
    if m and m.start() >= 2:
        pre = t[:m.start()]
        pre = re.sub(r'^(?:拟任|拟聘|拟|姓名|职务|项目组成员|该项目)', '', pre)
        for k in (4, 3, 2):
            if len(pre) >= k:
                nm = clean_name_tail(pre[-k:])
                if valid_name(nm):
                    return nm
    for k in range(ci - 1, -1, -1):
        c = row['cells'][k]
        if x0 - c[1] > 260:
            break
        nm = clean_name_tail(re.sub(r'\s+', '', c[2]))
        if valid_name(nm):
            return nm
    return ''


def _first_column_names(rows, y0, y1, lx1, h, est_gap):
    """标签右侧、同一视觉带内最左中文列的全部姓名 -> ([(姓名, y)], 该列 x0)

    分层放宽纵向带：先只看同一视觉行；同一行没有姓名时再放宽（用于表格单元格
    内换行拆字，如「邵建 / 克」）。
    """
    cy = (y0 + y1) / 2
    tiers = [3.0,
             max(est_gap * 0.62, 8.0),
             max(est_gap * 0.8, (y1 - y0) / 2 + h * 1.7)]

    def grab(band_half):
        cand = []
        for r in rows:
            if abs(r['y'] - cy) > band_half:
                continue
            for (x0, x1, txt) in r['cells']:
                if x0 < lx1 - 3:
                    continue
                if not is_cjk(txt) or len(txt) > 4:
                    continue
                if txt in BAD_WORDS:
                    continue
                cand.append((x0, r['y'], txt))
        return cand

    cand = []
    for band in tiers:
        cand = grab(band)
        if cand:
            break
    if not cand:
        return [], None
    cand.sort()
    base = cand[0][0]
    # 只取与首列 x0 几乎对齐的单元格：跨行拆字时下一行首字 x0 会偏移半个字宽，
    # 而相邻单元格（「先生」「女士」或下一列）x0 会偏移一个字宽以上。
    col = [c for c in cand if abs(c[0] - base) <= max(3.0, h * 0.75)]
    col.sort(key=lambda c: (c[1], c[0]))
    names = []
    for c in col:
        nm = clean_name_tail(c[2])
        if valid_name(nm) and nm not in [x[0] for x in names]:
            names.append((nm, c[1]))
    if names:
        # 跨行拆字：表格单元格里「邵建 / 克」这种居中换行，续字 x0 会比首字右移
        # 半个字宽；而下一行另一个人的姓名 x0 与上一行完全对齐，据此区分。
        fixed = []
        for nm, ny in names:
            if len(nm) == 2:
                near = sorted((r for r in rows if 0 < r['y'] - ny <= max(est_gap * 1.4, h * 1.6)),
                              key=lambda r: r['y'])
                for r in near:
                    hit = ''
                    for (x0, x1, txt) in r['cells']:
                        if 0 < x0 - base <= 8 and len(txt) == 1 and is_cjk(txt):
                            cand2 = nm + txt
                            if valid_name(cand2):
                                hit = cand2
                                break
                    if hit:
                        nm = hit
                        break
            fixed.append((nm, ny))
        return fixed, base
    nm = clean_name_tail(''.join(c[2] for c in col))[:4]
    if valid_name(nm):
        return [(nm, cy)], base
    for k in (3, 2):
        t = clean_name_tail(nm[:k])
        if valid_name(t):
            return [(t, cy)], base
    return [], None


def _continuation_names(rows, start_idx, base_x, h, limit=3):
    """角色行下方没有标签、但姓名列仍有姓名的续行（第二个签字人）"""
    out = []
    year_re = re.compile(r'(?:19|20)\d{2}\s*年?')
    for k in range(start_idx + 1, min(len(rows), start_idx + 1 + limit)):
        r = rows[k]
        if not r['cells']:
            continue
        if any(LABEL_RE[r].search(re.sub(r'\s+', '', c[2])) for c in r['cells'] for r in LABEL_RE):
            break
        has_year = any(year_re.search(c[2]) for c in r['cells'])
        if not has_year:
            break
        hit = ''
        for (x0, x1, txt) in r['cells']:
            if abs(x0 - base_x) <= max(4.0, h * 1.0) and is_cjk(txt) and 2 <= len(txt) <= 4:
                if valid_name(txt):
                    hit = txt
                break
        if not hit:
            break
        out.append(hit)
    return out


def _row_major(rows, role_cells):
    """R 版式：角色在左，姓名在右"""
    got, score = {}, {}
    if not rows:
        return got, score
    h = rows[0]['h']
    ys = [r['y'] for r in rows]
    gaps = sorted(g for g in (abs(ys[k + 1] - ys[k]) for k in range(len(ys) - 1)) if 8 < g < 120)
    est = gaps[len(gaps) // 2] if gaps else h * 2.2
    for rc in role_cells:
        ri, ci, role = rc['ri'], rc['ci'], rc['role']
        row = rows[ri]
        off = sum(len(c[2]) for c in row['cells'][:ci])
        tail = re.sub(r'\s+', '', row_text(row)[off + len(rc['text']):off + len(rc['text']) + 12])
        if NEG_AFTER.match(tail):
            continue
        names, base = _first_column_names(rows, rc['y0'], rc['y1'], rc['x1'], h, est)
        names = [(n, y) for n, y in names if n != role]
        if not names:
            nm_prev = _name_before_label(rows, rc['ri'], rc['ci'], role)
            if nm_prev:
                y_prev = rows[rc['ri']]['y']
                names = [(nm_prev, y_prev)]
                base = rows[rc['ri']]['cells'][rc['ci']][0]
        if not names:
            continue
        cy = (rc['y0'] + rc['y1']) / 2
        if role == ROLE_S:
            # 签字人可有多名，按自上而下顺序
            take = [n for n, _y in sorted(names, key=lambda x: x[1])]
        else:
            # 合伙人与复核人各只取一名，取离标签纵向中心最近的那个，避免串到相邻行
            take = [min(names, key=lambda x: abs(x[1] - cy))[0]]
        for nm in take:
            if nm not in got.setdefault(role, []):
                got[role].append(nm)
                score[role] = 85
        if base is not None and role == ROLE_S:
            for extra in _continuation_names(rows, max(ri, _row_index_at(rows, rc['y1'])), base, h):
                if extra not in got[role]:
                    got[role].append(extra)
    return got, score


def _row_index_at(rows, y):
    best, bi = 1e9, 0
    for i, r in enumerate(rows):
        d = abs(r['y'] - y)
        if d < best:
            best, bi = d, i
    return bi


def _transposed(rows, role_cells):
    """T 版式：角色在表头行，姓名在「姓名」行，按列对齐"""
    got = {}
    for i, row in enumerate(rows):
        cells = row['cells']
        if not cells:
            continue
        if re.sub(r'\s+', '', cells[0][2]) != '姓名':
            continue
        names = [(x0, x1, t) for (x0, x1, t) in cells[1:] if is_cjk(t) and 2 <= len(t) <= 4]
        names = [n for n in names if valid_name(n[2])]
        if not names:
            continue
        hdr = [(rc['x0'], rc['role']) for rc in role_cells
               if rc['y0'] < row['y'] and row['y'] - rc['y0'] < 140]
        if not hdr:
            continue
        byx = {}
        for x, role in sorted(hdr):
            byx.setdefault(role, x)
        roles = sorted(byx.items(), key=lambda kv: kv[1])
        if len(roles) == len(names):
            for (role, _x), (_x0, _x1, nm) in zip(roles, names):
                got.setdefault(role, [])
                if nm not in got[role]:
                    got[role].append(nm)
        else:
            for (x0, x1, nm) in names:
                c = (x0 + x1) / 2
                best = min(roles, key=lambda kv: abs(kv[1] - c))
                if abs(best[1] - c) < 120:
                    got.setdefault(best[0], [])
                    if nm not in got[best[0]]:
                        got[best[0]].append(nm)
    return got


def _prose(text):
    """P 版式"""
    got = {}
    if not text:
        return got
    BRIDGE2 = (r'(?:(?:[从执]业)?(?:经历|履历|简历|简介)|信息|情况|人员|'
               r'姓\s*名|名\s*称)?\s*(?:[（(][^）)]{0,14}[）)])?\s*[:：]?\s*')
    for role, rx in LABEL_RE.items():
        pat = re.compile(r'(?:' + rx.pattern + r')\s*(?:[123])?\s*(?:[:：]|为|是)?\s*'
                         + BRIDGE2 + r'(?:姓\s*名\s*[:：]?\s*)?'
                         r'([\u4e00-\u9fff]{2,4}?)(?:先生|女士)?' + BOUNDARY)
        for m in pat.finditer(text):
            nm = clean_name_tail(m.group(1))
            if not valid_name(nm):
                continue
            lst = got.setdefault(role, [])
            if nm not in lst:
                lst.append(nm)
            if len(lst) >= 3:
                break
    return got


def _joint(text):
    """C 版式：项目合伙人及签字注册会计师 1：王璟"""
    got = {}
    pat = re.compile(
        r'(项\s*目\s*合\s*伙\s*人)\s*(?:及|和|与|、)\s*'
        r'(?:(?:拟\s*)?(?:签\s*字\s*注\s*册\s*会\s*计\s*师|签\s*字\s*会\s*计\s*师))?\s*'
        r'(?:[123])?\s*(?:[:：]|为|是)?\s*(?:姓\s*名\s*[:：]?\s*)?'
        r'([\u4e00-\u9fff]{2,4}?)(?:先生|女士)?(?=[，,。；;：:、\s（）()]|\d|$)')
    for m in pat.finditer(text or ''):
        nm = clean_name_tail(m.group(2))
        if not valid_name(nm):
            continue
        for role in (ROLE_P, ROLE_S):
            lst = got.setdefault(role, [])
            if nm not in lst:
                lst.append(nm)
    return got


def _inverted(text):
    """姓名在角色标签之前的叙述式：
       「合伙人刘志坚拟担任项目质量控制复核人」「项目合伙人由张三担任」
    """
    got = {}
    t = text or ''
    # B: 姓名 + 拟担任/担任/出任 + 角色
    pat_b = re.compile(
        r'(?:(?:指派|委派|安排|任命|聘任|聘请|确定|拟由|由|经)?\s*'
        r'(?:合伙人|注册会计师|会计|老师)?)?\s*'
        r'([\u4e00-\u9fff]{2,4}?)(?:先生|女士)?\s*(?:拟|将)?\s*(?:担任|作为|出任|任)\s*'
        r'(?:本项目|项目)?(?:的)?\s*'
        r'((?:审\s*计\s*)?(?:项\s*目\s*)?(?:合\s*伙\s*人)|'
        r'(?:第\s*[一二三]\s*)?(?:签\s*字\s*注\s*册\s*会\s*计\s*师|签\s*字\s*会\s*计\s*师)|'
        r'(?:审\s*计\s*)?(?:项\s*目\s*)?(?:独\s*立\s*)?(?:质\s*量\s*)?(?:控\s*制\s*)?复\s*核'
        r'(?:\s*人\s*员|\s*负\s*责\s*人|\s*人|\s*合\s*伙\s*人))')
    for m in pat_b.finditer(t):
        nm = clean_name_tail(m.group(1))
        if not valid_name(nm):
            continue
        lab = re.sub(r'\s+', '', m.group(2))
        role = None
        for r, rx in LABEL_RE.items():
            if rx.fullmatch(lab):
                role = r
                break
        if role:
            lst = got.setdefault(role, [])
            if nm not in lst:
                lst.append(nm)
    # A: 角色 + 由 + 姓名 + 担任
    pat_a = re.compile(
        r'(项\s*目\s*合\s*伙\s*人|'
        r'(?:第\s*[一二三]\s*)?(?:签\s*字\s*注\s*册\s*会\s*计\s*师|签\s*字\s*会\s*计\s*师)|'
        r'(?:项\s*目\s*)?(?:质\s*量\s*)?(?:控\s*制\s*)?复\s*核(?:\s*人\s*员|\s*负\s*责\s*人|\s*人))'
        r'\s*(?:系|为|由)\s*([\u4e00-\u9fff]{2,4})(?:先生|女士)?\s*(?:担任|作为|出任|任)?')
    for m in pat_a.finditer(t):
        nm = m.group(2)
        if not valid_name(nm):
            continue
        lab = re.sub(r'\s+', '', m.group(1))
        for r, rx in LABEL_RE.items():
            if rx.fullmatch(lab):
                lst = got.setdefault(r, [])
                if nm not in lst:
                    lst.append(nm)
                break
    return got


def _name_before_role_text(text):
    """文本流里的「李进华项目合伙人」写法 -> 角色/姓名"""
    got = {}
    if not text:
        return got
    role_alt = (r'(项目合伙人|质量控制复核人|项目质量复核人|项目质量控制复核人|'
                r'质控复核人|质量复核人|签字注册会计师|签字会计师)')
    pat = re.compile(r'([\u4e00-\u9fff]{2,4}?)(?:先生|女士)?' + role_alt)
    for m in pat.finditer(text):
        nm = clean_name_tail(m.group(1))
        lab = m.group(2)
        role = None
        for r, rx in LABEL_RE.items():
            if rx.fullmatch(lab) or rx.search(lab):
                role = r
                break
        if not role or not valid_name(nm):
            continue
        lst = got.setdefault(role, [])
        if nm not in lst:
            lst.append(nm)
    return got


def _text_strategies(text):
    """无版面信息时的全部文本通道合并结果"""
    got = {}
    for g in (_prose(text), _joint(text), _inverted(text), _name_before_role_text(text)):
        for role, names in g.items():
            for nm in names:
                if valid_name(nm) and nm not in got.setdefault(role, []):
                    got[role].append(nm)
    return got


def extract_from_rows(rows):
    role_cells = _find_role_cells(rows)
    text = rows_text(rows)
    got, score = {}, {}
    rm_g, rm_s = _row_major(rows, role_cells)
    strategies = [
        (rm_g, rm_s, 85),
        (_transposed(rows, role_cells), None, 90),
        (_prose(text), None, 70),
        (_joint(text), None, 65),
        (_inverted(text), None, 68),
    ]
    for g, s, sc in strategies:
        for role, names in (g or {}).items():
            for nm in names:
                nm = clean_name_tail(nm)
                if not valid_name(nm):
                    continue
                if nm not in got.setdefault(role, []):
                    got[role].append(nm)
                per = s.get(role, sc) if s else sc
                score[role] = max(score.get(role, 0), per)
    return got, score, role_cells


def _section_text(raw):
    """截取「(二)项目信息 / 项目组成员」段落，用于兜底抽取"""
    if not raw:
        return ''
    m = re.search(r'[（(]\s*二\s*[）)]\s*项目|项目组成员|项目组人员|项目成员情况|项目人员信息'
                  r'|[一二三四2-9]\s*[.、．]\s*项目信息|拟项目合伙人信息|项目合伙人信息', raw)
    st = m.start() if m else max(0, raw.find('项目合伙人') - 120)
    seg = raw[st:st + 1800]
    cut = re.search(r'(?:2\s*[.．、]\s*诚信记录|3\s*[.．、]\s*独立性|[（(][三四][）)]\s*|审计收费)', seg)
    if cut and cut.start() > 60:
        seg = seg[:cut.start()]
    return seg


def _fallback_text_roles(raw, need):
    """版面通道缺失时，在受限的项目信息段落里用锚点窗口兜底"""
    got = {}
    sec = _section_text(raw)
    if not sec:
        return got
    sec_n = re.sub(r'[ \t\u3000]+', '', sec)
    for role in need:
        rx = LABEL_RE[role]
        for m in rx.finditer(sec_n):
            win = sec_n[m.end(): m.end() + 60]
            if NEG_AFTER.match(win[:12]):
                continue
            if re.match(r'\s*[:：]?\s*$', win[:4]):
                continue
            # 截到下一个人物标签为止，避免顺着文本流抓到下一行（下一个角色）的人名
            cut = len(win)
            for rx2 in LABEL_RE.values():
                m2 = rx2.search(win)
                if m2 and m2.start() < cut:
                    cut = m2.start()
            win = win[:cut]
            for tok in re.finditer(r'[\u4e00-\u9fff]{2,4}', win):
                nm = tok.group(0)
                nxt = win[tok.end():tok.end() + 1]
                if nxt and nxt not in '先生女自于在从进加现长曾已开起担为持拥具获参负任及和与并而0123456789，,。；;、：:（）()':
                    continue
                nm = clean_name_tail(nm)
                if not valid_name(nm):
                    continue
                got.setdefault(role, [])
                if nm not in got[role]:
                    got[role].append(nm)
                break
            if got.get(role):
                break
    return got


def extract_roles(pdf_path, max_pages=None, co_names=()):
    """-> ({role:[names]}, src, score, note)"""
    res = extract_document(pdf_path, max_pages=max_pages, co_names=co_names)
    got = {r: [p['name'] for p in res['persons'] if p['role'] == r] for r in ROLES}
    score = {}
    for p in res['persons']:
        score[p['role']] = max(score.get(p['role'], 0), p.get('confidence') or 0)
    return got, res['src'], score, res['note']


# ---------------------------------------------------------------- 人员 + 时间节点
def _row_index_at_y(rows, y):
    best, bi = 1e9, 0
    for i, r in enumerate(rows):
        d = abs(r['y'] - y)
        if d < best:
            best, bi = d, i
    return bi


def _times_row_major(rows, ri, name_x, cols, est):
    """取姓名同行的纯年份单元格；同行不足两个时再放宽到相邻行"""
    y = rows[ri]['y']
    tiers = [3.0, max(est * 0.55, 8.0)]
    times, raw = {f: '' for f in T.FIELDS}, []
    for band in tiers:
        cells = []
        for r in rows:
            if abs(r['y'] - y) > band:
                continue
            cells.extend(r['cells'])
        times, raw = T.from_row_cells(cells, name_x + 6, cols)
        if sum(1 for v in times.values() if v) >= 2:
            break
    return times, raw


def _times_transposed(rows, name_y, col_center, cols_by_row=None):
    """田字表：姓名行下方连续的「一年一格」取值行，按标准顺序对应四个节点

    标准表格的四行固定为：成为注册会计师 / 开始从事上市公司审计 /
    开始在本所执业 / 开始为本公司提供审计服务。取每行中与本角色列对齐的
    年份单元格，可避开标签单元格换行造成的错位。
    """
    times = {f: '' for f in T.FIELDS}
    val_rows = []
    for r in rows:
        if r['y'] <= name_y or r['y'] - name_y > 240:
            continue
        head = re.sub(r'\s+', '', row_text(r, ''))[:20]
        if re.search(r'近三年|从业情况|签署|复核上市', head):
            break
        years = [(x0, x1, T.parse_year(txt)) for (x0, x1, txt) in r['cells']]
        years = [c for c in years if c[2]]
        if len(years) >= 2:
            val_rows.append((r, years))
        if len(val_rows) >= 4:
            break
    for field, (r, years) in zip(T.FIELDS, val_rows[:4]):
        best, bd = '', 1e9
        for (x0, x1, v) in years:
            c = (x0 + x1) / 2
            d = abs(c - col_center)
            if d < bd:
                bd, best = d, v
        if best and bd <= 100:
            times[field] = best
    return times


def _labelled_rows(rows, start_y):
    """在「姓名」行下方找出「何时成为…」等标签行 -> [(y, field)]（兼容跨行换行）"""
    out = []
    for i, r in enumerate(rows):
        if r['y'] <= start_y:
            continue
        txt = re.sub(r'\s+', '', row_text(r, ''))
        nxt = ''
        if i + 1 < len(rows) and rows[i + 1]['y'] - r['y'] < 30:
            nxt = re.sub(r'\s+', '', row_text(rows[i + 1], ''))
        for f, rx in T.HEADER_PAT.items():
            if rx.search(txt) or (nxt and rx.search(txt + nxt)):
                if not any(f == x[1] for x in out):
                    out.append((r['y'], f))
                break
    return out


def _persons_from_rows(rows):
    """从单页版面行中抽 (职位, 姓名, 时间节点)"""
    role_cells = _find_role_cells(rows)
    got = []
    if not rows:
        return got
    h = rows[0]['h']
    ys = [r['y'] for r in rows]
    gaps = sorted(g for g in (abs(ys[k + 1] - ys[k]) for k in range(len(ys) - 1)) if 8 < g < 120)
    est = gaps[len(gaps) // 2] if gaps else h * 2.2
    first_role = min((rc['ri'] for rc in role_cells), default=0)
    cols = T.header_columns(rows, first_role)

    # --- 行式表格 ---
    for rc in role_cells:
        ri, role = rc['ri'], rc['role']
        names, base = _first_column_names(rows, rc['y0'], rc['y1'], rc['x1'], h, est)
        names = [(n, y) for n, y in names if n != role]
        if not names:
            nm_prev = _name_before_label(rows, rc['ri'], rc['ci'], role)
            if nm_prev:
                y_prev = rows[rc['ri']]['y']
                names = [(nm_prev, y_prev)]
                base = rows[rc['ri']]['cells'][rc['ci']][0]
        if not names:
            continue
        cy = (rc['y0'] + rc['y1']) / 2
        if role == ROLE_S:
            chosen = sorted(names, key=lambda x: x[1])
        else:
            chosen = [min(names, key=lambda x: abs(x[1] - cy))]
        for nm, ny in chosen:
            ri2 = _row_index_at_y(rows, ny)
            name_x = base if base is not None else rc['x1']
            times, raw = _times_row_major(rows, ri2, name_x, cols, est)
            got.append({'role': role, 'name': nm, 'times': times, 'years': raw,
                        'source': 'table'})
        if role == ROLE_S and base is not None:
            ri_end = _row_index_at(rows, rc['y1'])
            for extra in _continuation_names(rows, max(ri, ri_end), base, h):
                ri3 = None
                for k in range(ri + 1, min(len(rows), ri + 4)):
                    if any(extra == t for (_x0, _x1, t) in rows[k]['cells']):
                        ri3 = k
                        break
                times, raw = ({f: '' for f in T.FIELDS}, [])
                if ri3 is not None:
                    times, raw = _times_row_major(rows, ri3, base, cols, est)
                if not any(p['name'] == extra and p['role'] == role for p in got):
                    got.append({'role': role, 'name': extra, 'times': times,
                                'years': raw, 'source': 'table'})

    # --- 田字表格（角色在表头，姓名在「姓名」行）---
    for i, row in enumerate(rows):
        cells = row['cells']
        if not cells or re.sub(r'\s+', '', cells[0][2]) != '姓名':
            continue
        names = [(x0, x1, t) for (x0, x1, t) in cells[1:] if is_cjk(t) and 2 <= len(t) <= 4]
        names = [n for n in names if valid_name(n[2])]
        if not names:
            continue
        hdr = [(rc['x0'], rc['role']) for rc in role_cells
               if rc['y0'] < row['y'] and row['y'] - rc['y0'] < 140]
        if not hdr:
            continue
        byx = {}
        for x, role in sorted(hdr):
            byx.setdefault(role, x)
        roles = sorted(byx.items(), key=lambda kv: kv[1])
        labelled = _labelled_rows(rows, row['y'])
        if len(roles) == len(names):
            pairs = list(zip(roles, names))
        else:
            pairs = []
            for (x0, x1, nm) in names:
                c = (x0 + x1) / 2
                role, rx = min(roles, key=lambda kv: abs(kv[1] - c))
                if abs(rx - c) < 120:
                    pairs.append(((role, rx), (x0, x1, nm)))
        for (role, rxc), (x0, x1, nm) in pairs:
            times = _times_transposed(rows, row['y'], (x0 + x1) / 2, labelled)
            got.append({'role': role, 'name': nm, 'times': times, 'years': [],
                        'source': 'table_t'})
    return got


def _persons_from_text(raw):
    """叙述式：角色标签后的描述段落 -> 职位/姓名/四节点"""
    out = []
    sec = _section_text(raw)
    if not sec:
        return out
    text = re.sub(r'[ \t\u3000]+', '', sec)
    for role, rx in LABEL_RE.items():
        for m in rx.finditer(text):
            win = text[m.end(): m.end() + 220]
            if NEG_AFTER.match(win[:12]):
                continue
            desc = win
            nm = ''
            mm = re.match(r'\s*(?:[123])?\s*(?:[:：]|为|是)?\s*'
                          r'(?:(?:[从执]业)?(?:经历|履历|简历|简介)|信息|情况|人员|姓名|名称)?\s*[:：]?\s*'
                          r'(?:姓名\s*[:：]?\s*)?'
                          r'([\u4e00-\u9fff]{2,4}?)(?:先生|女士)?' + BOUNDARY, win)
            if mm and valid_name(clean_name_tail(mm.group(1))):
                nm = clean_name_tail(mm.group(1))
                desc = win[mm.end():]
                # 截到下一个人物标签为止，避免串到下一个人的描述
                cut = len(desc)
                for rx2 in LABEL_RE.values():
                    m2 = rx2.search(desc)
                    if m2 and m2.start() < cut:
                        cut = m2.start()
                desc = desc[:cut]
            else:
                continue
            times = T.from_prose(desc)
            out.append({'role': role, 'name': nm, 'times': times,
                        'years': [], 'source': 'prose'})
    return out


def extract_document(pdf_path, max_pages=None, co_names=()):
    """完整抽取：{roles, persons[], src, note}"""
    if co_names:
        set_co_names(co_names)
    persons = []
    try:
        doc = pymupdf.open(pdf_path)
    except Exception as e:
        return {'persons': [], 'roles': {}, 'src': 'error', 'note': f'open:{e}'[:60]}
    try:
        n = len(doc) if max_pages is None else min(len(doc), max_pages)
        if n == 0:
            return {'persons': [], 'roles': {}, 'src': 'error', 'note': 'empty'}
        pages_rows = [page_rows(doc[i]) for i in range(n)]
        if not any(pages_rows):
            return {'persons': [], 'roles': {}, 'src': 'scan', 'note': 'no_text_layer'}
        srcs = set()
        for rows in pages_rows:
            if not rows:
                continue
            if not any(any(LABEL_RE[r].search(row_text(rw)) for r in LABEL_RE) for rw in rows):
                continue
            ps = _persons_from_rows(rows)
            if ps:
                persons.extend(ps)
                srcs.add('layout')
        raw = '\n'.join(doc[i].get_text() for i in range(n))
        if not persons:
            ps = _persons_from_text(raw)
            if ps:
                persons = ps
                srcs.add('prose')
            else:
                g = _text_strategies('\n'.join(row_text(r) for rows in pages_rows for r in rows)) \
                    or _text_strategies(raw)
                for role, names in g.items():
                    for nm in names:
                        persons.append({'role': role, 'name': nm,
                                        'times': {f: '' for f in T.FIELDS},
                                        'years': [], 'source': 'text'})
                if persons:
                    srcs.add('rawtext')
        # 用叙述式补缺失的时间节点（来源标签只记一次）
        if persons and any(not any(p['times'].values()) for p in persons):
            prose = {(p['role'], p['name']): p for p in _persons_from_text(raw)}
            for p in persons:
                q = prose.get((p['role'], p['name']))
                if not q:
                    continue
                for f in T.FIELDS:
                    if not p['times'].get(f) and q['times'].get(f):
                        p['times'][f] = q['times'][f]
                if '+prose' not in p['source'] and p['source'] != 'prose':
                    p['source'] = p['source'] + '+prose'
        # 兜底：缺失的角色用「项目信息段落锚点窗口」补齐
        have_roles = {p['role'] for p in persons}
        need = [r for r in ROLES if r not in have_roles]
        if need:
            fb = _fallback_text_roles(raw, need)
            for role, names in fb.items():
                for nm in names:
                    persons.append({'role': role, 'name': nm,
                                    'times': {f: '' for f in T.FIELDS},
                                    'years': [], 'source': 'secfallback'})
            if fb:
                srcs.add('secfallback')
        # 签字注册会计师含项目合伙人本人：缺失时沿用合伙人
        have_roles = {p['role'] for p in persons}
        if ROLE_S not in have_roles and ROLE_P in have_roles:
            for p in [x for x in persons if x['role'] == ROLE_P]:
                persons.append({'role': ROLE_S, 'name': p['name'],
                                'times': dict(p['times']), 'years': [],
                                'source': p['source'] + '(*)'})
            srcs.add('concurrent')
        # 兜底：仍无时间节点的人员，用「姓名锚定上下文」再试一次
        if persons:
            flat = re.sub(r'[ \t\u3000]+', '', raw)
            for p in persons:
                if all(p['times'].values()):
                    continue
                nm = p['name']
                if not nm or nm not in flat:
                    continue
                pos = flat.find(nm)
                # 优先取紧跟角色标签的那一处
                label = re.compile(r'项目合伙人|签字注册会计师|签字会计师|质量控制复核|'
                                   r'质量复核|复核人|质控复核')
                for m in re.finditer(re.escape(nm), flat):
                    if label.search(flat[max(0, m.start() - 24):m.start()]):
                        pos = m.start()
                        break
                ctx = flat[pos + len(nm): pos + len(nm) + 240]
                t2 = T.from_prose(ctx)
                if any(t2.values()):
                    for f in T.FIELDS:
                        if not p['times'].get(f) and t2.get(f):
                            p['times'][f] = t2[f]
                    if 'ctx' not in p['source']:
                        p['source'] = p['source'] + '+ctx'

        # 去重 + 角色人数上限
        seen = set()
        uniq = []
        for p in persons:
            key = (p['role'], p['name'])
            if key in seen:
                continue
            seen.add(key)
            uniq.append(p)
        cap = {ROLE_P: 2, ROLE_S: 4, ROLE_R: 1}
        cnt = {}
        out = []
        for p in uniq:
            c = cnt.get(p['role'], 0)
            if c >= cap.get(p['role'], 1):
                continue
            cnt[p['role']] = c + 1
            out.append(p)
        roles = {r: [p['name'] for p in out if p['role'] == r] for r in ROLES}
        return {'persons': out, 'roles': roles,
                'src': '+'.join(sorted(srcs)) if srcs else 'none', 'note': ''}
    finally:
        doc.close()



if __name__ == '__main__':
    import sys
    for f in sys.argv[1:]:
        g, src, sc, note = extract_roles(f, max_pages=12)
        print('=' * 60)
        print(f.split('\\')[-1][:60], '|', src, '|', note)
        for role in ROLES:
            print('  ', role, g.get(role), sc.get(role))
