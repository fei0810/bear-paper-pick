#!/usr/bin/env python3
"""paper-radar 取数助手 v2 — 取数、结构化落盘、去重、状态维护。

分工边界：脚本负责传输与全部结构化持久化（candidates.json / seen.json 的写入
一律由脚本完成，标题不经模型转写）；agent 只做判断与散文。
退出码：
  0  正常
  2  可选通道跳过（S2 无 key、被拒或无结果）→ 战报记「跳过」
  3  上游在重试后仍失败（网络、429、5xx，或 400/404）→ 该通道战报记失败；
     400/404 先查参数与标识，其余情况才改走 references/api-recipes.md 手动路径
  64 命令用法错误 → 按 stderr 修正命令后重跑
  65 状态文件或数据校验失败 → 停下报告，不得绕过脚本手写状态
  70 内部错误 → 停下报告
状态文件（candidates.json、seen.json）的读改写已加文件锁，可以并行调用 --save；
Windows 没有 fcntl，那里仍须串行。

用法:
  work <doi|Wid> [--save F]                       单篇详情，--save 追加候选池
  search <词...> [--since D] [--to D] [--limit N] [--save F]
  cites <Wid> [--since D] [--to D] [--limit N] [--noabstract] [--save F]
                                                  施引查询，--save 时记录 cites_seed
  author <姓名|Aid>                                 按姓名找 author.id；给 Aid 出档案（机构轨迹/被引/代表作，deep-read 人物层用）
  authorworks <Aid> [--since D] [--limit N] [--save F]
  refs <Wid> [Wid...] [--save F]                       种子参考文献并集与共引计数（经典层通道）
  s2 --pos id[,id...] [--neg id,...] [--limit N] [--save F]
                                                       S2 推荐，429 → exit 2
  bibtex <id> [id...]
  pool archive <candidates.json> [--max N]        按已推、旧 search、最老 search 的顺序归档
  seen init|add|feedback|list <F> [参数]          seen.json 维护：
      seen init F [--force] / seen add F <id> <title> [--doi DOI] / seen feedback F <id> up|down / seen list F

--save 规则：加载候选池文件（无则新建 []），按 id 去重；同目录有 seen.json 则剔除已推；
每条写入 {id,doi,title,year,venue,authors,abstract,pdf,channel,cites_seed}；
打印「新增 X｜重复跳过 Y｜已推剔除 Z」。
"""
import contextlib, datetime, hashlib, html, json, os, re, socket, sys, tempfile, time, unicodedata, urllib.error, urllib.parse, urllib.request, uuid
from pathlib import Path
try:
    import fcntl
except ImportError:  # Windows：没有 fcntl，退化为单写者，SKILL.md 要求串行调用 --save
    fcntl = None

MAILTO = ""  # Authentication uses an optional API key, not an invented contact.
RUN_ID = uuid.uuid4().hex
RAW_DIR = None
REQUEST_LOG = []
LIMIT_NOTES = []
BASE = "https://api.openalex.org"
S2BASE = "https://api.semanticscholar.org"

# 请求超时：OpenAlex 大页面偶尔超过 20 秒，40 秒留出余量。
REQUEST_TIMEOUT = 40
# 1 次正常请求 + 2 次重试：多数瞬时 429/5xx 在第二次请求时恢复。
MAX_ATTEMPTS = 3
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_BACKOFF_SECONDS = 10
# socket.timeout 在 Python 3.9 不是 TimeoutError 的子类，需单列。
NETWORK_ERRORS = (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError)


class UsageError(Exception):
    """命令行用法错误，退出码 64。"""


class StateError(ValueError):
    """状态文件或数据不满足前置条件，退出码 65。"""


class UpstreamError(RuntimeError):
    """上游服务在重试后仍失败，退出码 3。"""


def _backoff(attempt, retry_after=None):
    try:
        if retry_after:
            return max(0, min(float(retry_after), MAX_BACKOFF_SECONDS))
    except ValueError:
        pass
    return min(2 ** attempt, MAX_BACKOFF_SECONDS)


def get(url, headers=None, attempts=MAX_ATTEMPTS, sleeper=time.sleep):
    """GET JSON；429、5xx、超时与连接错误按指数退避重试，其余 HTTP 错误立即抛出。"""
    h = {"User-Agent": "paper-radar/1.8.0"}
    if urllib.parse.urlsplit(url).hostname == "api.openalex.org":
        from config import setting
        key = setting("OPENALEX_API_KEY")
        if key:
            h["Authorization"] = "Bearer " + key
    if headers:
        h.update(headers)
    last_error = None
    for attempt in range(attempts):
        req = urllib.request.Request(url, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
                try:
                    payload = json.load(r)
                    if RAW_DIR:
                        destination = Path(RAW_DIR) / RUN_ID / (hashlib.sha256(url.encode()).hexdigest() + ".json")
                        write_json({"url": url, "fetched_at": utc_now(), "response": payload}, destination)
                    REQUEST_LOG.append({"url": url, "count": (payload.get("meta") or {}).get("count"), "returned": len(payload.get("results", []))})
                    return payload
                except ValueError as error:  # 上游返回了非 JSON（如错误页）
                    raise UpstreamError(f"响应不是 JSON：{url}") from error
        except urllib.error.HTTPError as error:
            if error.code not in RETRY_STATUS:
                raise
            last_error = error
            delay = _backoff(attempt, error.headers.get("Retry-After") if error.headers else None)
        except NETWORK_ERRORS as error:
            last_error = error
            delay = _backoff(attempt)
        if attempt < attempts - 1:
            sleeper(delay)
    raise last_error


@contextlib.contextmanager
def locked(path):
    """跨进程串行化同一状态文件的读改写；原子替换只防文件损坏，不防更新丢失。"""
    lock_path = os.path.abspath(path) + ".lock"
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    with open(lock_path, "a") as handle:
        if fcntl:
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl:
                fcntl.flock(handle, fcntl.LOCK_UN)

def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def identity_keys(item):
    """Canonical identifiers only; never guess that related works are versions."""
    values = [item] if isinstance(item, str) else [item.get("id"), item.get("doi"), *item.get("aliases", [])]
    keys = set()
    for value in values:
        token = str(value or "").strip().lower()
        for prefix in ("https://openalex.org/", "http://openalex.org/", "https://doi.org/", "http://doi.org/", "doi:"):
            if token.startswith(prefix):
                token = token[len(prefix):]
        if token.startswith("https://arxiv.org/abs/"):
            token = "arxiv:" + token.rsplit("/", 1)[-1]
        if token.startswith("10.48550/arxiv."):
            token = "arxiv:" + token.split("arxiv.", 1)[1]
        if token.startswith("arxiv:"):
            token = re.sub(r"v[0-9]+$", "", token)
        if token:
            keys.add(token)
    return keys


def list_works(params, limit):
    """Cursor paging with a caller budget and explicit truncation report."""
    results, cursor, seen = [], "*", set()
    total = 0
    while len(results) < limit and cursor and cursor not in seen:
        seen.add(cursor)
        query = {**params, "cursor": cursor, "per-page": min(100, limit - len(results))}
        page = get(BASE + "/works?" + urllib.parse.urlencode(query))
        total = page["meta"]["count"]
        results.extend(page.get("results", []))
        cursor = page.get("meta", {}).get("next_cursor")
        if not page.get("results"):
            break
    LIMIT_NOTES.append({"total": total, "returned": len(results), "truncated": len(results) < total, "limit": limit})
    print(f"# 命中 {total}，本次取回 {len(results)}，截断 {len(results) < total}")
    return {"meta": {"count": total, "truncated": len(results) < total}, "results": results}


def norm_id(s):
    s = str(s).strip()
    for pre in ("https://doi.org/", "http://doi.org/", "doi:", "DOI:",
                "https://openalex.org/", "http://openalex.org/"):
        if s.lower().startswith(pre.lower()): s = s[len(pre):]
    if s.startswith("10."): return f"doi:{s}"
    return s

def abstract(inv):
    if not inv: return ""
    pos = {i: w for w, idxs in inv.items() for i in idxs}
    return " ".join(pos[i] for i in sorted(pos))

def doi_key(value):
    value = str(value or "").strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if value.startswith(prefix):
            value = value[len(prefix):]
    return value or None

def load_list(path, label):
    """Load a JSON list and fail before a damaged state is overwritten."""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        value = json.load(f)
    if not isinstance(value, list):
        raise StateError(f"{label} 必须是 JSON 数组：{path}")
    if any(not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"] for item in value):
        raise StateError(f"{label} 的每条记录须有非空字符串 id：{path}")
    return value

def write_json(value, path):
    """Atomically replace a JSON file so an interrupted run keeps the old state."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".paper-radar-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=1)
            f.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise

def record(w, channel):
    loc = w.get("best_oa_location") or {}
    text = abstract(w.get("abstract_inverted_index"))
    return {
        "id": w["id"].split("/")[-1],
        "doi": (w.get("doi") or "").replace("https://doi.org/", ""),
        "title": w.get("display_name") or "",
        "year": w.get("publication_year"),
        "publication_date": w.get("publication_date"),
        "type": w.get("type"),
        "is_retracted": bool(w.get("is_retracted")),
        "topics": w.get("topics") or [],
        "citation_percentile": (w.get("citation_normalized_percentile") or {}).get("value"),
        "cited_by_count": w.get("cited_by_count"),
        "fetched_at": utc_now(),
        "run_id": RUN_ID,
        "venue": ((w.get("primary_location") or {}).get("source") or {}).get("display_name"),
        "authors": [a["author"]["display_name"] for a in (w.get("authorships") or [])],
        "abstract": text,
        # OpenAlex 的 language 由标题与摘要自动检测；没有摘要时不再标注摘要语言。
        "abstract_language": (w.get("language") or None) if text else None,
        "pdf": loc.get("pdf_url") or loc.get("landing_page_url"),
        "channel": channel,
        "cites_seed": None,
    }

def brief(r, with_abstract=True):
    pdf = f"\npdf: {r['pdf']}" if r.get("pdf") else ""
    ab = ((f"\nabstract: {r['abstract']}" if r.get("abstract") else "\nabstract: (无)")
          if with_abstract else "")
    return (f"id: {r['id']}  doi: {r['doi'] or '-'}\ntitle: {r['title']}\n"
            f"authors: {', '.join(r['authors'][:8])}\nvenue: {r['venue'] or '-'}  "
            f"year: {r['year']}{ab}{pdf}")

def seed_identity(directory):
    """读取 seeds.txt 的全部种子标识。

    每行一粒种子或一个版本，空格分隔，# 之后为注释。第一个标识通常是 OpenAlex ID，
    同行可追加 DOI 与 arXiv:xxxx；S2 等通道返回的同一篇论文据此也能被剔除。
    """
    ids, dois = set(), set()
    path = os.path.join(directory, "seeds.txt")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                for token in line.split("#", 1)[0].split():
                    if token.lower().startswith("arxiv:"):
                        ids.add(token)
                        dois.add("10.48550/arxiv." + token.split(":", 1)[1].lower())
                    elif (doi_key(token) or "").startswith("10."):
                        dois.add(doi_key(token))
                    else:
                        ids.add(token)
    return ids, dois

def save(records, path):
    with locked(path):
        _save_unlocked(records, path)

def _save_unlocked(records, path):
    cand = load_list(path, "candidates.json")
    seen_ids, seen_dois = set(), set()
    seen_path = os.path.join(os.path.dirname(path) or ".", "seen.json")
    if os.path.exists(seen_path):
        seen_state = load_list(seen_path, "seen.json")
        seen_ids = {e["id"] for e in seen_state if "id" in e}
        seen_dois = {doi_key(e.get("doi")) for e in seen_state if doi_key(e.get("doi"))}
    seed_ids, seed_dois = seed_identity(os.path.dirname(path) or ".")
    seed_keys = set().union(*(identity_keys(x) for x in seed_ids | seed_dois))
    seen_keys = set().union(*(identity_keys(x) for x in load_list(seen_path, "seen.json")))
    have = {c["id"] for c in cand}
    have_dois = {doi_key(c.get("doi")) for c in cand if doi_key(c.get("doi"))}
    dup = seen = sd = added = 0
    for r in records:
        if identity_keys(r) & seed_keys: sd += 1
        elif identity_keys(r) & seen_keys: seen += 1
        elif any(identity_keys(r) & identity_keys(c) for c in cand):
            existing = next(c for c in cand if identity_keys(r) & identity_keys(c))
            merge_provenance(existing, r)
            dup += 1
        else:
            merge_provenance(r, r)
            cand.append(r)
            have.add(r["id"])
            if doi_key(r.get("doi")):
                have_dois.add(doi_key(r["doi"]))
            added += 1
    write_json(cand, path)
    warn = "｜⚠ 池超 300，须按规则 8 归档并在战报注明" if len(cand) > 300 else ""
    print(f"# 新增 {added}｜重复跳过 {dup}｜已推剔除 {seen}｜种子剔除 {sd}｜候选池现存 {len(cand)}{warn}")

def merge_provenance(existing, incoming):
    now = incoming.get("fetched_at") or utc_now()
    existing.setdefault("first_seen", existing.get("fetched_at") or now)
    existing["last_seen"] = now
    existing["channels"] = list(dict.fromkeys([*existing.get("channels", []), *filter(None, [existing.get("channel"), incoming.get("channel")])]))
    existing["cites_seeds"] = list(dict.fromkeys([*existing.get("cites_seeds", []), *filter(None, [existing.get("cites_seed"), incoming.get("cites_seed")])]))
    existing["aliases"] = list(dict.fromkeys([*existing.get("aliases", []), incoming["id"]]))
    provenance = existing.setdefault("provenance", [])
    event = {"channel": incoming.get("channel"), "run_id": incoming.get("run_id") or RUN_ID, "fetched_at": now}
    if event not in provenance:
        provenance.append(event)


def verify_metadata(path, paper_ids):
    """Cross-check DOI/title and date precision; preserve source values."""
    pool = load_list(path, "candidates.json")
    by_id = {item["id"]: item for item in pool}
    changes = {}
    for paper_id in dict.fromkeys(paper_ids):
        if paper_id not in by_id or not by_id[paper_id].get("doi"):
            raise StateError(f"{paper_id}: 不在池中或没有 DOI")
        item = by_id[paper_id]
        doi = doi_key(item["doi"])
        source = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
        message = get(source)["message"]
        title = (message.get("title") or [""])[0]
        clean = lambda text: re.sub(r"[^\w]", "", html.unescape(re.sub(r"<[^>]+>", "", text)).casefold())
        verified = {"source": source, "verified_at": utc_now(), "title": title, "doi": message.get("DOI")}
        conflict = clean(title) != clean(item["title"]) or doi_key(message.get("DOI")) != doi
        date = None
        precision = "unknown"
        # Prefer a complete online date to a print issue's month padded to day 1.
        for field in ("published-online", "published", "published-print"):
            parts = (message.get(field) or {}).get("date-parts") or []
            if parts and len(parts[0]) == 3:
                date = datetime.date(*parts[0]).isoformat()
                precision = "day"
                verified["date_field"] = field
                break
        verified.update(publication_date=date, date_precision=precision)
        changes[paper_id] = (doi, conflict, verified)
    with locked(path):
        pool = load_list(path, "candidates.json")
        for item in pool:
            if item["id"] not in changes:
                continue
            doi, conflict, verified = changes[item["id"]]
            if doi_key(item.get("doi")) != doi:
                raise StateError("核验期间候选 DOI 已变化")
            item["metadata_verification"] = verified
            item["metadata_conflict"] = conflict
            item["date_precision"] = verified["date_precision"]
            if not conflict and verified["publication_date"]:
                item.setdefault("publication_date_original", item.get("publication_date"))
                item.setdefault("year_original", item.get("year"))
                item["publication_date"] = verified["publication_date"]
                item["year"] = int(verified["publication_date"][:4])
        write_json(pool, path)
    print(f"# 核验 {len(changes)} 篇｜身份冲突 {sum(value[1] for value in changes.values())}")
    return [item for item in pool if item["id"] in changes]


def bibtex_escape(value):
    replacements = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
    return "".join(replacements.get(c, c) for c in str(value))


def bibtex_record(r, year_override=None, used_keys=None):
    year = year_override or r.get("year") or ""
    authors = r.get("authors") or []
    surname = authors[0].split()[-1] if authors else "anon"
    base = re.sub("[^a-zA-Z]", "", unicodedata.normalize("NFKD", surname).encode("ascii", "ignore").decode()) or "anon"
    base += str(year)
    used_keys = used_keys if used_keys is not None else set()
    key, suffix = base, 0
    while key in used_keys:
        suffix += 1
        key = f"{base}{chr(96 + suffix)}" if suffix <= 26 else f"{base}{suffix}"
    used_keys.add(key)
    entry_type = "misc" if r.get("type") in {"preprint", "report", "dataset"} else "article"
    fields = [("title", r["title"]), ("author", " and ".join(authors)), ("year", year), ("doi", r.get("doi"))]
    if r.get("venue"):
        fields.append(("journal", r["venue"]))
    url = f"https://doi.org/{doi_key(r['doi'])}" if r.get("doi") else r.get("pdf")
    if url:
        fields.append(("url", url))
    fields = [(name, bibtex_escape(value)) for name, value in fields if value]
    lines = [f"@{entry_type}{{{key},"]
    for i, (name, value) in enumerate(fields):
        if name == "title":
            value = "{" + value + "}"
        lines.append(f"  {name} = {{{value}}}" + ("," if i < len(fields) - 1 else ""))
    return "\n".join([*lines, "}"])


def bibtex(w, year_override=None, used_keys=None):
    return bibtex_record(record(w, "-"), year_override, used_keys)


def archive_pool(path, maximum=300):
    """Archive only the documented low-value candidates; never silently discard the rest."""
    with locked(path):
        _archive_pool_unlocked(path, maximum)

def _archive_pool_unlocked(path, maximum):
    candidates = load_list(path, "candidates.json")
    if len(candidates) <= maximum:
        print(f"# 候选池 {len(candidates)}，未超过 {maximum}，无需归档")
        return
    directory = os.path.dirname(path) or "."
    seen_ids = {e.get("id") for e in load_list(os.path.join(directory, "seen.json"), "seen.json")}

    def priority(item):
        year = item.get("year")
        year = year if isinstance(year, int) else 9999
        channel = str(item.get("channel") or "")
        if item.get("id") in seen_ids:
            return (0, year, str(item.get("id") or ""))
        if channel.startswith("search:") and year < 2015:
            return (1, year, str(item.get("id") or ""))
        if channel.startswith("search:"):
            return (2, year, str(item.get("id") or ""))
        return (3, year, str(item.get("id") or ""))

    need = len(candidates) - maximum
    eligible = [item for item in sorted(candidates, key=priority) if priority(item)[0] < 3]
    archived = eligible[:need]
    archived_ids = {id(item) for item in archived}
    kept = [item for item in candidates if id(item) not in archived_ids]
    if not archived:
        print(f"# 候选池 {len(candidates)} 超过 {maximum}，但没有符合规则的可归档记录")
        return
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_path = os.path.join(directory, "archive", f"candidates-{timestamp}-{uuid.uuid4().hex[:8]}.json")
    write_json({"archived_at": datetime.datetime.now().isoformat(timespec="seconds"), "records": archived}, archive_path)
    write_json(kept, path)
    remaining = len(kept)
    suffix = "；仍超上限，需人工判断未归档通道" if remaining > maximum else ""
    print(f"# 已归档 {len(archived)} 条 → {archive_path}｜活跃池 {remaining}{suffix}")

def main():
    global RAW_DIR
    REQUEST_LOG.clear()
    LIMIT_NOTES.clear()
    args = sys.argv[1:]
    if args in (["--help"], ["-h"]):
        print(__doc__); return
    if not args: print(__doc__); return
    cmd, rest = args[0], args[1:]
    valid_flags = {"--save", "--since", "--to", "--limit", "--min-year", "--year", "--max", "--doi", "--pos", "--neg", "--report", "--raw-dir", "--noabstract", "--verbose", "--force"}
    unknown = [x for x in rest if x.startswith("--") and x not in valid_flags]
    if unknown: raise UsageError("未知参数：" + ", ".join(unknown))
    def opt(n, d=None):
        if n not in rest:
            return d
        i = rest.index(n)
        if i + 1 >= len(rest) or rest[i + 1].startswith("--"):
            raise UsageError(f"{n} 后缺少取值")
        return rest[i + 1]
    def int_opt(n, d):
        value = opt(n, d)
        try:
            return int(value)
        except (TypeError, ValueError):
            raise UsageError(f"{n} 需要整数，收到 {value!r}") from None
    def words():
        out, skip_next = [], False
        val_flags = ("--save", "--since", "--to", "--limit", "--min-year", "--year", "--max", "--doi", "--pos", "--neg", "--report", "--raw-dir")
        for a in rest:
            if skip_next: skip_next = False; continue
            if a in val_flags: skip_next = True; continue
            if a.startswith("--"): continue
            out.append(a)
        return out
    def first_arg(what):
        if not rest or rest[0].startswith("--"):
            raise UsageError(f"{cmd} 需要 {what}")
        return rest[0]
    def need_words(what):
        w = words()
        if not w:
            raise UsageError(f"{cmd} 需要 {what}")
        return w
    since, to, limit = opt("--since"), opt("--to"), int_opt("--limit", "25")
    save_path = opt("--save")
    if limit < 1 or limit > 5000:
        raise UsageError("--limit 必须在 1 到 5000 之间")
    RAW_DIR = opt("--raw-dir")
    for value in (since, to):
        if value:
            try:
                datetime.date.fromisoformat(value)
            except ValueError:
                raise UsageError("日期须为 YYYY-MM-DD") from None
    if since and to and since > to:
        raise UsageError("--since 不可晚于 --to")
    def display(text):
        if not save_path or "--verbose" in rest:
            print(text)
    out = []
    returned_count = None

    if cmd == "verify":
        positional = need_words("候选池路径与至少一个候选 ID")
        if len(positional) < 2:
            raise UsageError("用法: verify candidates.json ID [ID...]")
        out = verify_metadata(positional[0], positional[1:])
    elif cmd == "work":
        first_arg("DOI 或 OpenAlex ID")
        rec = record(get(f"{BASE}/works/{norm_id(rest[0])}"), "work")
        display(brief(rec)); out = [rec]
    elif cmd == "search":
        terms = need_words("检索词")
        filters = []
        if since: filters.append(f"from_publication_date:{since}")
        elif opt("--min-year"): filters.append(f"from_publication_date:{opt('--min-year')}-01-01")
        if to: filters.append(f"to_publication_date:{to}")
        params = {"search": " ".join(terms)}
        if filters: params["filter"] = ",".join(filters)
        d = list_works(params, limit)
        print(f"# 命中 {d['meta']['count']}，显示 {len(d['results'])}")
        for w in d["results"]:
            r = record(w, f"search:{' '.join(words())}")
            display(brief(r, "--noabstract" not in rest)); display("---"); out.append(r)
    elif cmd == "cites":
        seed = norm_id(first_arg("种子 OpenAlex ID"))
        filt = f"cites:{seed}"
        if since: filt += f",from_publication_date:{since}"
        if to: filt += f",to_publication_date:{to}"
        d = list_works({"filter": filt, "sort": "publication_date:desc"}, limit)
        print(f"# 命中 {d['meta']['count']}，显示 {len(d['results'])}")
        for w in d["results"]:
            r = record(w, f"cites:{seed}")
            r["cites_seed"] = seed
            display(brief(r, "--noabstract" not in rest) + f"\ncites_seed: {seed}"); display("---"); out.append(r)
    elif cmd == "author":
        w = need_words("作者姓名或 OpenAlex 作者 ID")
        nid = norm_id(w[0]) if len(w) == 1 else ""
        if nid.startswith("A") and nid[1:].isdigit():
            a = get(f"{BASE}/authors/{nid}")
            insts = a.get("last_known_institutions") or []
            print(f"{a['id'].split('/')[-1]} | {a['display_name']} | works:{a.get('works_count')} | cites:{a.get('cited_by_count')} | h:{(a.get('summary_stats') or {}).get('h_index')}")
            print("现机构:", "; ".join(i.get("display_name", "-") for i in insts) or "-")
            affs = {}
            for af in a.get("affiliations") or []:
                nm = af["institution"]["display_name"]; ys = af.get("years") or []
                if nm not in affs or (ys and min(ys) < affs[nm][0]):
                    affs[nm] = (min(ys), max(ys)) if ys else None
            for k, v in sorted(affs.items()):
                print(f"  履历: {k} ({v[0]}–{v[1]})" if v else f"  履历: {k}")
            top = get(f"{BASE}/works?{urllib.parse.urlencode({'filter': f'author.id:{nid}', 'sort': 'cited_by_count:desc', 'per-page': 3, 'select': 'id,display_name,publication_date,cited_by_count'})}")
            for w2 in top.get("results", []):
                print(f"  代表作: {w2['publication_date']} | {w2['display_name'][:70]} | cites {w2['cited_by_count']}")
        else:
            d = get(f"{BASE}/authors?{urllib.parse.urlencode({'search': ' '.join(w)})}")
            for a in d["results"][:5]:
                insts = a.get("last_known_institutions") or []
                print(f"{a['id'].split('/')[-1]} | {a['display_name']} | works:{a.get('works_count')} | {insts[0].get('display_name','-') if insts else '-'}")
    elif cmd == "authorworks":
        filt = f"author.id:{norm_id(first_arg('OpenAlex 作者 ID'))}"
        if since: filt += f",from_publication_date:{since}"
        d = list_works({"filter": filt, "sort": "publication_date:desc"}, limit)
        print(f"# 命中 {d['meta']['count']}，显示 {len(d['results'])}")
        for w in d["results"]:
            r = record(w, f"authorworks:{rest[0]}")
            display(brief(r, "--noabstract" not in rest)); display("---"); out.append(r)
    elif cmd == "s2":
        from config import setting
        key = setting("SEMANTIC_SCHOLAR_API_KEY")
        if not key:
            print("ERROR: S2 推荐端点需要 API key（环境变量 SEMANTIC_SCHOLAR_API_KEY）；无 key 战报记「跳过」", file=sys.stderr)
            sys.exit(2)
        pos = opt("--pos", ""); neg = opt("--neg", "")
        if not pos:
            raise UsageError("s2 需要 --pos id[,id...]")
        fields = "title,abstract,year,venue,externalIds,authors"
        hdr = {"x-api-key": key}
        urls = [f"{S2BASE}/recommendations/v1/papers/forpaper/{urllib.parse.quote(x, safe=':./')}?{urllib.parse.urlencode({'fields': fields, 'limit': str(max(3, limit // len(pos.split(','))))})}" for x in pos.split(",")]
        if neg: print("注意: 路径式 forpaper 端点不支持负样本，--neg 已忽略", file=sys.stderr)
        papers, failures = [], []
        for u in urls:
            try:
                papers.extend(get(u, hdr).get("recommendedPapers", []))
            except urllib.error.HTTPError as e:
                if e.code in (401, 403, 429):
                    print(f"ERROR: S2 拒绝({e.code})，战报记「跳过」", file=sys.stderr); sys.exit(2)
                failures.append(f"{e.code}: {u}")
        if not papers:
            if failures:
                raise UpstreamError("S2 所有种子请求失败：" + "; ".join(failures))
            print("WARN: S2 未返回候选，战报记「跳过」", file=sys.stderr); sys.exit(2)
        if failures:
            print(f"WARN: S2 有 {len(failures)} 粒种子失败，已使用其余结果", file=sys.stderr)
        seen_t = set()
        for p in papers:
            ext = p.get("externalIds") or {}
            rid = (ext.get("ArXiv") and f"arXiv:{ext['ArXiv']}") or (ext.get("DOI") and f"doi:{ext['DOI']}") or p.get("paperId") or ""
            if rid in seen_t: continue
            seen_t.add(rid)
            r = {"id": rid, "doi": ext.get("DOI") or "", "title": p.get("title") or "",
                 "year": p.get("year"), "venue": p.get("venue"),
                 "authors": [a.get("name") for a in (p.get("authors") or [])],
                 "abstract": p.get("abstract") or "", "abstract_language": None,  # S2 不提供语言字段
                 "pdf": None, "channel": "s2", "cites_seed": None}
            display(brief(r, "--noabstract" not in rest)); display("---"); out.append(r)
    elif cmd == "refs":
        from collections import Counter
        cnt = Counter()
        for x in need_words("至少一个种子 ID"):
            w = get(f"{BASE}/works/{norm_id(x)}")
            for rid in (w.get("referenced_works") or []):
                cnt[rid.split("/")[-1]] += 1
        top = [i for i, _ in cnt.most_common(100)]
        if not top:
            print("# 种子没有可解析的参考文献")
            return
        d = get(f"{BASE}/works?{urllib.parse.urlencode({'filter': 'ids.openalex:' + '|'.join(top), 'per-page': 100})}")
        ranked = sorted(d["results"], key=lambda w: (-cnt[w["id"].split("/")[-1]], -(w.get("cited_by_count") or 0)))
        nco = sum(1 for v in cnt.values() if v >= 2)
        print(f"# 参考文献并集 {len(cnt)} 篇，共引(>=2) {nco} 篇，解析 {len(ranked)} 篇")
        for w in ranked:
            r = record(w, "refs")
            r["ref_count"] = cnt[r["id"]]
            display(brief(r) + f"\n被种子共引: {r['ref_count']} 次"); display("---"); out.append(r)
        returned_count = len(out)
        LIMIT_NOTES.append({"reference_union": len(cnt), "resolved": len(out), "resolution_limit": 100, "pool_rule": "co-cited or top 20"})
        if save_path:  # 池卫生：共引>=2 全留，否则只留排序前 20
            keep = [r for r in out if r.get("ref_count", 0) >= 2]
            save(keep if keep else out[:20], save_path)
            out = []
    elif cmd == "canon":
        from collections import Counter
        since_d = since or (datetime.date.today() - datetime.timedelta(days=548)).isoformat()
        seeds = {norm_id(x) for x in need_words("至少一个种子 ID")}
        cnt, citers = Counter(), 0
        citer_ids = set()
        for sd in seeds:
            filt = f"cites:{sd},from_publication_date:{since_d}"
            if to: filt += f",to_publication_date:{to}"
            d = get(f"{BASE}/works?{urllib.parse.urlencode({'filter': filt, 'per-page': 100, 'sort': 'cited_by_count:desc'})}")
            for w in d["results"]:
                if w["id"] in citer_ids: continue
                citer_ids.add(w["id"])
                citers += 1
                for rid in (w.get("referenced_works") or []):
                    r = rid.split("/")[-1]
                    if r not in seeds: cnt[r] += 1
        top = [i for i, _ in cnt.most_common(100)]
        d2 = get(f"{BASE}/works?{urllib.parse.urlencode({'filter': 'ids.openalex:' + '|'.join(top), 'per-page': 100})}") if top else {"results": []}
        cutoff = to or datetime.date.today().isoformat()
        valid = [w for w in d2["results"] if not w.get("publication_date") or w["publication_date"] <= cutoff]
        future = len(d2["results"]) - len(valid)
        ranked = sorted(valid, key=lambda w: (-cnt[w["id"].split("/")[-1]], -(w.get("cited_by_count") or 0)))
        LIMIT_NOTES.append({"sampling": "最多每种子 100 篇施引文献", "citers": citers, "resolved": len(ranked), "returned": min(len(ranked), limit)})
        print(f"# 施引反推：{citers} 篇施引文献，refs 并集 {len(cnt)}，解析 {len(ranked)}（窗口自 {since_d}，剔除晚于 {cutoff} 的元数据 {future} 条）")
        for w in ranked[:limit]:
            r = record(w, "canon")
            r["ref_count"] = cnt[r["id"]]
            display(brief(r, "--noabstract" not in rest) + f"\n被施引池共引: {r['ref_count']} 次"); display("---"); out.append(r)
    elif cmd == "bibtex":
        yv = opt("--year")
        if yv and len(need_words("ID")) != 1:
            raise UsageError("--year 仅可覆盖单篇条目")
        used_keys, requested = set(), set()
        for x in need_words("至少一个 DOI 或 OpenAlex ID"):
            identifier = norm_id(x)
            if identifier in requested:
                continue
            requested.add(identifier)
            print(bibtex(get(f"{BASE}/works/{identifier}"), yv, used_keys))
    elif cmd == "pool":
        if len(rest) < 2 or rest[0] != "archive":
            raise UsageError("用法: pool archive <candidates.json> [--max N]")
        maximum = int_opt("--max", "300")
        if maximum < 1:
            raise UsageError("--max 必须为正整数")
        archive_pool(rest[1], maximum)
    elif cmd == "seen":
        need = {"init": 2, "list": 2, "add": 4, "feedback": 4}
        if not rest or rest[0] not in need or len(rest) < need[rest[0]]:
            raise UsageError("用法: seen init F / seen add F <id> <title> / seen feedback F <id> up|down / seen list F")
        sub = rest[0]; path = rest[1]
        if sub == "init":
            with locked(path):
                if os.path.exists(path) and "--force" not in rest:
                    raise StateError(f"{path} 已存在；如确认重置请加 --force")
                write_json([], path); print("seen.json 已初始化")
        elif sub == "add":
            supplied_doi = opt("--doi")
            normalized_doi = doi_key(supplied_doi)
            title_end = rest.index("--doi") if "--doi" in rest else len(rest)
            with locked(path):
                data = load_list(path, "seen.json")
                if any(e.get("id") == rest[2] or (normalized_doi and doi_key(e.get("doi")) == normalized_doi) for e in data):
                    print(f"seen 已有 {rest[2]}，未重复登记")
                    return
                entry = {"id": rest[2], "title": " ".join(rest[3:title_end]), "date": datetime.date.today().isoformat(), "feedback": []}
                if normalized_doi:
                    entry["doi"] = normalized_doi
                data.append(entry)
                write_json(data, path); print(f"seen +1 → {len(data)}")
        elif sub == "feedback":
            if rest[3] not in {"up", "down"}:
                raise UsageError("feedback 只能是 up 或 down")
            with locked(path):
                data = load_list(path, "seen.json"); hit = False
                for e in data:
                    if e.get("id") == rest[2]:
                        prior = e.get("feedback")
                        if not isinstance(prior, list):
                            prior = [] if prior is None else [{"value": prior, "date": e.get("date"), "legacy": True}]
                        prior.append({"value": rest[3], "date": datetime.date.today().isoformat()})
                        e["feedback"] = prior
                        hit = True
                if not hit:
                    raise StateError(f"{rest[2]} 不在 seen.json")
                write_json(data, path); print(f"{rest[2]} → {rest[3]}（已追加反馈）")
        elif sub == "list":
            for e in load_list(path, "seen.json"):
                feedback = e.get("feedback")
                if isinstance(feedback, list):
                    feedback = feedback[-1].get("value") if feedback else "-"
                print(f"{feedback or '-'}\t{e.get('id', '-')}\t{e.get('title', '')[:60]}")
    else:
        raise UsageError(f"未知子命令 {cmd!r}；运行 fetch.py 不带参数查看用法")

    if save_path and out:
        save(out, save_path)
    if opt("--report"):
        write_json({"schema_version": "paper-radar-fetch-run-v1", "run_id": RUN_ID, "created_at": utc_now(), "channel": cmd, "status": "complete", "returned": len(out) if returned_count is None else returned_count, "requests": REQUEST_LOG, "limits": LIMIT_NOTES}, opt("--report"))

def run_cli():
    """执行 main() 并把异常映射为文档约定的退出码。"""
    try:
        main()
    except SystemExit:
        raise
    except UsageError as e:
        print(f"USAGE: {e}", file=sys.stderr); sys.exit(64)
    except urllib.error.HTTPError as e:
        print(f"UPSTREAM: HTTP {e.code}（{getattr(e, 'url', '') or '-'}）", file=sys.stderr); sys.exit(3)
    except (UpstreamError,) + NETWORK_ERRORS as e:
        print(f"UPSTREAM: {e}", file=sys.stderr); sys.exit(3)
    except ValueError as e:  # 含 StateError 与 JSON 解析错误
        print(f"STATE: {e}", file=sys.stderr); sys.exit(65)
    except Exception as e:
        print(f"INTERNAL: {type(e).__name__}: {e}", file=sys.stderr); sys.exit(70)

if __name__ == "__main__":
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)  # 管道下游提前关闭时静默退出
    except (ImportError, AttributeError, ValueError):
        pass
    run_cli()
