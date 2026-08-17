# -*- coding: utf-8 -*-
"""
投研工作台 v19 · 今日热点推送 + 二级页《盘点今日热点》 + 历史日报（7天·逐日真实归档） 真实数据源（每日 08:30 自动运行）
- 主源：新浪新闻多频道滚动接口（真实、公开、可靠），返回标题/摘要/来源发布时间(ctime)/原文链接
- 热度辅助：微博实时热搜榜（带 num 热度值），作为"最热/有爆点"信号
- 首页「今日热点推送」：全频道实时池(今天真实新闻) -> 六主题各取 Top1 -> 固定顺序排布 -> 回写 sampleHotNews
- 二级页「盘点今日热点」：六主题(政治/军事/经济/科技/社会/文化)各取 Top（最多 6 条，最新+最热+有爆点）
        -> 回写 sampleHotTopics（每主题 3-6 条，不足则取真实池中弱相关今日资讯填充）
- 蓝框 time 取信息来源出处原始发布时间(HH:MM)；整段重建 hot-head 避免残留多余时间戳
"""
import json, re, sys, subprocess, datetime, ssl
from collections import Counter
from urllib.request import Request, urlopen

REPO  = r"G:/Workbuddy/publish/touyan-workbench-app-v19"
INDEX = REPO + r"/index.html"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

SINA_TPL  = "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=%d&num=40&order=0"
WEIBO     = "https://weibo.com/ajax/side/hotSearch"
# 全频道混合实时池（cutoff 36h 只保留今天真实新闻）
MAIN_LIDS = [2510, 2511, 2513, 2514, 2515, 2516, 2517, 2518]

CUTOFF_HOURS = 36
MIN_POOL = 6

# 6 主题固定顺序（用户给定）：政治→军事→经济→科技→社会→文化
THEMES = [
    ("政治", "tag-pol", "关注国际关系与政策信号的边际变化"),
    ("军事", "tag-mil", "关注地缘与国防动态对避险情绪的扰动"),
    ("经济", "tag-eco", "关注宏观与流动性对权益资产定价的传导"),
    ("科技", "tag-tec", "关注技术突破与产业趋势带来的主题机会"),
    ("社会", "tag-soc", "关注民生事件的社会影响与政策关注"),
    ("文化", "tag-cul", "关注文娱消费与大众情绪走向"),
]

# 军事/文化实时新闻在新浪滚动中稀少，用弱相关信号从真实池中筛选最相关今日资讯
WEAK = {
    "军事": ["国际", "地缘", "外交", "制裁", "冲突", "国防", "安全", "谈判", "伊朗", "美国", "俄乌", "中东", "朝鲜", "边境", "航母", "导弹", "军", "战争", "袭击"],
    "文化": ["社会", "民生", "消费", "生活", "商业", "人事", "教育", "医疗", "文娱", "网红", "票房", "综艺", "明星", "体育", "旅游", "就业", "养老", "娱乐", "电影", "电视剧", "音乐", "游戏", "动漫", "展览", "文旅", "热播", "收视", "演唱会"],
}

KW = {
    "军事": ["军事","军队","导弹","军演","航母","战机","潜艇","北约","五角大楼","战争","袭击","无人机","网络攻击","黑客","网络安全","边境","演习","军工","空袭","部队","武器","航天器","摧毁"],
    "科技": ["AI","人工智能","大模型","芯片","半导体","机器人","量子","航天","卫星","火箭","华为","苹果","英伟达","特斯拉","自动驾驶","6G","光刻机","算法","元宇宙","新能源","发布","推出","量产","技术","智能","超人"],
    "经济": ["央行","降准","降息","A股","股市","沪指","深成指","创业板","汇率","人民币","GDP","CPI","PPI","美联储","美债","债券","基金","理财","银行","证监会","财政部","通胀","经济","金融","上市","IPO","期货","黄金","原油","大宗","货币","市场","财报","业绩","营收","净利","车企","丰田","本田","日产","主力资金","股价","资金","日元","美元","关税"],
    "文化": ["电影","电视剧","综艺","音乐","歌手","演员","明星","票房","春晚","世界杯","奥运","赛事","体育","影视","演唱会","网红","动漫","游戏","专辑","节目","上映","主演","导演","影迷","观影","开播","收官","夺冠","总决赛","开赛","新歌","单曲","娱乐","热播","口碑","巡演","展览","文旅","网剧","话剧","脱口秀","真人秀","追星","顶流","票房榜","收视","开画"],
    "政治": ["外交部","国际","联合国","中美","中欧","中俄","谈判","制裁","访问","峰会","声明","政府","国会","总统","首相","大选","大使","建交","外交","发言人","伊朗","以色列","乌克兰"],
}
BREAKING = ["突发","重磅","独家","刚刚","官宣","破纪录","首次","震惊","刷屏","定了","紧急","通报","致歉","落马","逮捕","开战","宣战","断交","升级","爆发","暴跌","暴涨","创历史新高","首次公开"]

def bjnow():
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("Asia/Shanghai"))
    except Exception:
        return datetime.datetime.utcnow() + datetime.timedelta(hours=8)

def jget(url, ref, timeout=18):
    req = Request(url, headers={"User-Agent": UA, "Referer": ref})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def clean(s):
    if not s:
        return ""
    s = re.sub(r"^新浪\S*讯\s*", "", s)
    s = re.sub(r"\d+月\d+[日号]\S*消息，?", "", s)
    s = s.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    return re.sub(r"\s{2,}", " ", s).strip()

def classify(text):
    for t in ["军事", "科技", "经济", "文化", "政治"]:
        if any(k in text for k in KW[t]):
            return t
    return "社会"

def build_hot(weibo, top=30):
    fps = []
    for i, it in enumerate(weibo[:top]):
        word = it.get("word", "")
        if len(word) < 3:
            continue
        w = 3 if i < 10 else (2 if i < 20 else 1)
        for L in (3, 4):
            for s in range(len(word) - L + 1):
                fps.append((word[s:s + L], w))
    return fps

def hot_score(title, fps):
    best = 0
    for sub, w in fps:
        if sub and sub in title:
            best = max(best, w)
    return best

def dir_of(text):
    bull = ["降准","降息","回购","利好","增长","新高","超预期","上调","扩容","批复","签约","中标","净利增","营收增","大涨","创新高","放宽","支持","加码","突破","发布","推出","增产"]
    bear = ["处罚","退市","下跌","利空","亏损","下调","风险","违规","立案","降级","大跌","收紧","暂停","警示","暴跌","制裁","冲突","袭击","爆发"]
    if any(k in text for k in bull):
        return "bull"
    if any(k in text for k in bear):
        return "bear"
    return "flat"

def collect(items, pool, now, fps, theme_override=None, cutoff_hours=CUTOFF_HOURS, seen_set=None):
    if seen_set is None:
        seen_set = set()
    for it in items:
        try:
            ctime = int(it.get("ctime") or 0)
        except Exception:
            ctime = 0
        if ctime <= 0:
            continue
        if now.timestamp() - ctime > cutoff_hours * 3600:
            continue
        title = clean(it.get("title", ""))
        if not title or title in seen_set:
            continue
        seen_set.add(title)
        intro = clean(it.get("intro") or it.get("summary") or "")
        if len(intro) > 60:
            intro = intro[:60] + "…"
        url = it.get("url") or it.get("wapurl") or ""
        text = title + " " + intro
        theme = theme_override if theme_override else classify(text)
        age = (now.timestamp() - ctime) / 3600
        fresh = 3 if age <= 1 else (2.5 if age <= 3 else (2 if age <= 6 else (1.5 if age <= 12 else 1)))
        score = fresh
        if any(k in text for k in BREAKING):
            score += 1.5
        score += hot_score(title, fps)
        dt = datetime.datetime.fromtimestamp(ctime, tz=datetime.timezone(datetime.timedelta(hours=8)))
        hhmm = dt.strftime("%H:%M")
        pool.append({
            "ctime": ctime, "theme": theme, "title": title, "summary": intro,
            "url": url, "time": hhmm, "dir": dir_of(text), "score": score,
        })

def main():
    now = bjnow()

    # 1) 微博实时热搜（最热/有爆点信号）
    hot_list = []
    try:
        d = jget(WEIBO, "https://weibo.com/", 12)
        hot_list = (d.get("data") or {}).get("realtime") or []
        print("微博热搜获取 %d 条" % len(hot_list))
    except Exception as e:
        print("WARN: 微博热搜获取失败，仅用最新+爆点词打分: %s" % e)
    fps = build_hot(hot_list, 30)

    # 2) 全频道实时池（今天真实新闻）
    pool = []
    seen = set()
    for lid in MAIN_LIDS:
        try:
            d = jget(SINA_TPL % lid, "https://news.sina.com.cn/", 20)
            collect((d.get("result") or {}).get("data") or [], pool, now, fps, seen_set=seen)
        except Exception as e:
            print("WARN: lid %d 获取失败: %s" % (lid, e))

    cnt = Counter(p["theme"] for p in pool)
    print("实时池分布(分类):", dict(cnt), "总", len(pool))
    if len(pool) < MIN_POOL:
        print("FAILED: 候选池不足 (%d 条 < %d)，保留原数据不动" % (len(pool), MIN_POOL))
        sys.exit(2)

    # 3) 分桶：强主题用自身分类 Top1；军事/文化弱相关兜底；最后全局兜底
    buckets = {t: [] for t, _, _ in THEMES}
    for p in pool:
        buckets[p["theme"]].append(p)
    selected, used = [], set()
    for tag, cls, base in THEMES:
        arr = sorted(buckets.get(tag, []), key=lambda x: -x["score"])
        if not arr:
            weak = WEAK.get(tag, [])
            cand = [p for p in pool if id(p) not in used and any(k in (p["title"] + p["summary"]) for k in weak)]
            cand.sort(key=lambda x: -x["score"])
            arr = cand
        if not arr:
            cand = [p for p in pool if id(p) not in used]
            cand.sort(key=lambda x: -x["score"])
            arr = cand
        if not arr:
            continue
        pick = arr[0]
        used.add(id(pick))
        d = pick["dir"]
        impact = ("偏多：" if d == "bull" else ("偏空：" if d == "bear" else "中性：")) + base
        selected.append({
            "time": pick["time"], "tag": tag, "tagCls": cls,
            "title": pick["title"], "summary": pick["summary"],
            "impact": impact, "dir": d, "url": pick["url"],
        })
    if len(selected) < 6:
        print("WARN: 仅选出 %d 条，仍按现有结果覆盖发布" % len(selected))

    arr = json.dumps(selected, ensure_ascii=False).replace("</", "<\\/")
    ts = now.strftime("%Y-%m-%d %H:%M:%S")
    date_str = now.strftime("%Y-%m-%d")
    wk = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
    html = open(INDEX, encoding="utf-8").read()

    # ===== 历史日报：逐日真实归档 =====
    # 在覆盖 sampleHotTopics 之前，先从原文件取出"上一次"的六主题真实数据（即昨日热点）及其日期；
    # 若今日 != 文件内 LATEST_DATE（说明跨天），把昨日数据滚入 sampleHistoryDays 头部，并裁剪到 6 条过去（7天=今日+6过去）；
    # 同日重跑则不滚动，避免重复归档。
    def _extract_array(text, varname):
        m = re.search(r"var %s=(\[[\s\S]*?\]);" % varname, text)
        if not m:
            return None
        try:
            return json.loads(m.group(1))
        except Exception:
            return None

    m_prev_date = re.search(r"var LATEST_DATE='([^']*)'", html)
    m_prev_wk   = re.search(r"var LATEST_WEEKDAY='([^']*)'", html)
    prev_date = m_prev_date.group(1) if m_prev_date else date_str
    prev_wk   = m_prev_wk.group(1) if m_prev_wk else wk
    prev_topics = _extract_array(html, "sampleHotTopics")
    history = _extract_array(html, "sampleHistoryDays") or []
    new_history_json = None
    if prev_topics is not None and date_str != prev_date:
        entry = {"date": prev_date, "weekday": prev_wk, "topics": prev_topics}
        rolled = [entry] + list(history)
        rolled = rolled[:6]
        new_history_json = json.dumps(rolled, ensure_ascii=False).replace("</", "<\\/")
        print("OK: 归档 %s 当日六主题热点入历史（历史保留 %d 天）" % (prev_date, len(rolled)))
    else:
        print("INFO: 同日重跑或数据不可解析，历史日报不滚动（prev=%s today=%s）" % (prev_date, date_str))

    newblock = "var sampleHotNews=" + arr + ";\n"
    html2, n = re.subn(r"var sampleHotNews=\[[\s\S]*?\n?\];", newblock, html, count=1)
    if n != 1:
        print("FAILED: sampleHotNews 区块未替换 (n=%d)" % n)
        sys.exit(3)
    # 二级页《盘点今日热点》：六主题各取 Top（最多 6 条，最新+最热+有爆点）
    # 若某主题真实条目 < 3，先按弱相关关键词补，仍不足则用当日真实池未使用较高分词条补齐到 3（避免跨主题重复）
    topics = []
    used_titles = set()
    for tag, cls, base in THEMES:
        arr = sorted(buckets.get(tag, []), key=lambda x: -x["score"])
        if len(arr) < 3:
            weak = WEAK.get(tag, [])
            cand = [p for p in pool if id(p) not in [id(x) for x in arr] and any(k in (p["title"] + p["summary"]) for k in weak)]
            cand.sort(key=lambda x: -x["score"])
            arr = arr + cand
        if len(arr) < 3:
            g = [p for p in pool if id(p) not in [id(x) for x in arr] and p["title"] not in used_titles]
            g.sort(key=lambda x: -x["score"])
            arr = arr + g
        arr = arr[:6]
        items = []
        for p in arr:
            if p["title"] in used_titles:
                continue
            used_titles.add(p["title"])
            items.append({"title": p["title"], "summary": p["summary"], "time": p["time"], "url": p["url"], "dir": p["dir"], "highlight": False})
        topics.append({"theme": tag, "cls": cls, "items": items})
    topics_json = json.dumps(topics, ensure_ascii=False).replace("</", "<\\/")
    newblock_t = "var sampleHotTopics=" + topics_json + ";\n"
    html2, nt = re.subn(r"var sampleHotTopics=\[[\s\S]*?\n?\];", newblock_t, html2, count=1)
    if nt != 1:
        print("FAILED: sampleHotTopics 区块未替换 (n=%d)" % nt)
        sys.exit(4)
    print("OK: 二级页六主题热点，每主题条数 =", [len(t["items"]) for t in topics])
    # 整段重建 hot-head，杜绝残留多余时间戳（黄框尾巴）
    header_new = (
        '      <div class="hot-head">\n'
        '        <span class="hot-head-icon">🔥</span>\n'
        '        <span class="hot-head-title">今日热点推送</span>\n'
        '        <span class="hot-head-sub"><span>最后获取资讯时间</span><span>%s</span></span>\n'
        '      </div>' % ts
    )
    html2, sn = re.subn(r'      <div class="hot-head">[\s\S]*?\n      </div>', header_new, html2, count=1)
    if sn != 1:
        print("WARN: 表头未替换 (n=%d)" % sn)
    # 更新二级页日期、抓取时间、两模块表头时间
    html2 = re.sub(r"var LATEST_DATE='[^']*'", "var LATEST_DATE='%s'" % date_str, html2, count=1)
    html2 = re.sub(r"var LATEST_WEEKDAY='[^']*'", "var LATEST_WEEKDAY='%s'" % wk, html2, count=1)
    html2 = re.sub(r"var FETCH_TIME='[^']*'", "var FETCH_TIME='%s'" % ts, html2, count=1)
    # [v19] 二级页"盘点今日热点"模块表头时间戳已移除（按用户要求），不再替换；右上方 report-header-sub 由前端 JS 按 FETCH_TIME 动态渲染
    # 历史日报：写入逐日归档后的 sampleHistoryDays（若本次跨天滚动了）
    if new_history_json is not None:
        html2, nh = re.subn(r"var sampleHistoryDays=\[[\s\S]*?\n?\];", "var sampleHistoryDays=" + new_history_json + ";\n", html2, count=1)
        if nh != 1:
            print("WARN: sampleHistoryDays 区块未替换 (n=%d)" % nh)
    open(INDEX, "w", encoding="utf-8").write(html2)
    print("OK: 写入 %d 条真实资讯（政治→军事→经济→科技→社会→文化），获取时间 %s" % (len(selected), ts))
    print("主题分布:", dict(Counter(x["tag"] for x in selected)))

    subprocess.run(["git", "-C", REPO, "add", "-A"], check=True)
    subprocess.run(["git", "-C", REPO, "-c", "user.email=bot@workbuddy.local", "-c", "user.name=WorkBuddy", "commit", "-m", "每日热点资讯更新 %s" % ts], check=True)
    subprocess.run(["git", "-C", REPO, "push", "origin", "main"], check=True)
    print("PUSHED -> qiao3412-cmd.github.io/touyan-workbench-app-v19")

if __name__ == "__main__":
    main()
