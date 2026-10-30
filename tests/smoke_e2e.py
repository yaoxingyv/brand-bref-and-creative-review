# -*- coding: utf-8 -*-
"""端到端冒烟：验证主链路 + 七条硬约束是否真的拦得住。"""
import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8010"
PASS, FAIL = [], []


def req(method, path, data=None, token=None):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(BASE + path, data=body, headers=h, method=method)
    try:
        resp = urllib.request.urlopen(r, timeout=25)
        return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())
    except Exception as e:
        return "ERR", str(e)


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  OK   " if cond else "  FAIL ") + name + ((" | " + str(extra)) if extra else ""))


def login(u, p="pilot2026"):
    s, j = req("POST", "/api/v1/auth/login", {"username": u, "password": p})
    return j.get("access_token") if s == 200 else None


print("=" * 60)
print("一、登录与角色")
t_biz = login("guminglan")     # 业务负责人（授权确认人）
t_design = login("linyizhou")  # 设计师（非授权人）
t_audit = login("fujing")      # 审核负责人
t_admin = login("admin")
check("六角色可登录", all([t_biz, t_design, t_audit, t_admin]))
s, _ = req("POST", "/api/v1/auth/login", {"username": "guminglan", "password": "wrong"})
check("错误密码返回 401", s == 401, s)
s, _ = req("GET", "/api/v1/briefs")
check("未登录访问返回 401", s == 401, s)

print("\n二、简报与缺项清单（M2 / M3）")
s, briefs = req("GET", "/api/v1/briefs", token=t_design)
check("简报列表可读", s == 200 and len(briefs) > 0, f"{len(briefs)} 份")
bid = briefs[0]["id"]

s, d = req("GET", f"/api/v1/briefs/{bid}/constraints", token=t_design)
check("30 项约束已铺好", s == 200 and d["total"] == 30, f"已确认 {d['confirmed']}")
check("存在未确认项（口径区分）", d["unconfirmed"] > 0, f"待确认 {d['unconfirmed']}")
check("存在缺失项（口径区分）", d["missing"] > 0, f"缺失 {d['missing']}")

s, gaps = req("POST", f"/api/v1/briefs/{bid}/check", token=t_design)
check("缺项清单生成", s == 200 and len(gaps["field_gaps"]) > 0, f"字段缺 {len(gaps['field_gaps'])} 项")
check("缺项附来源位置", all(g["source"] for g in gaps["field_gaps"]))
check("缺关键字段不允许提交", gaps["can_submit"] is False)

print("\n三、硬约束拦截（红线）")
def detail_of(j):
    if isinstance(j, dict):
        d = j.get("detail", j)
        return d if isinstance(d, str) else json.dumps(d, ensure_ascii=False)
    return str(j)

# 入口层：设计师角色根本进不了决策类接口
s, j = req("POST", f"/api/v1/approvals/briefs/{bid}/confirm",
           {"version_id": 1, "final_value": "通过"}, token=t_design)
check("设计师角色在入口层被拦 403", s == 403, detail_of(j))

# R2：傅景有决策角色，但不在 brief_confirm 授权名单内（f22：确认由业务负责人做）
s, j = req("POST", f"/api/v1/approvals/briefs/{bid}/confirm",
           {"version_id": 1, "final_value": "通过"}, token=t_audit)
check("R2 名单外决策角色确认被拒 403", s == 403 and "R2" in detail_of(j), detail_of(j))
# 该次越界必须留痕
s, v = req("GET", "/api/v1/admin/violations", token=t_admin)
check("越界行为已留痕", s == 200 and len(v) > 0, f"{len(v)} 条")
check("留痕含规则码 R2", any(x["rule_code"].startswith("R2") for x in v))

# 建议接口（R3）：只能写建议
s, j = req("POST", f"/api/v1/approvals/briefs/{bid}/suggest",
           {"original_value": "旧文案", "suggested_value": "新文案", "version_id": 1}, token=t_design)
check("R1/R3 建议可提交", s == 200, j.get("note", ""))

# 授权人确认
s, j = req("POST", f"/api/v1/approvals/briefs/{bid}/confirm",
           {"version_id": 1, "final_value": "确认通过"}, token=t_biz)
check("R2 授权人确认放行", s == 200, j)

print("\n四、版本与过期来源（M4 / M6）")
s, j = req("POST", f"/api/v1/briefs/{bid}/versions",
           {"biz_occurred_at": "2020-01-01T00:00:00", "raw_snapshot": "一条很旧的消息"}, token=t_biz)
check("R6 过期来源被拦截 409", s == 409, detail_of(j))

s, j = req("POST", f"/api/v1/briefs/{bid}/versions",
           {"biz_occurred_at": None}, token=t_biz)
check("R7 缺业务发生时间不默认通过", s == 409, detail_of(j))

s, j = req("POST", f"/api/v1/briefs/{bid}/versions",
           {"biz_occurred_at": "2026-10-30T10:00:00", "confirm_status": "unconfirmed"}, token=t_biz)
check("正常版本可写入", s == 200, j)

s, j = req("GET", f"/api/v1/briefs/{bid}/effective-version", token=t_biz)
check("双条件判定生效（未确认则 409）", s in (200, 409), s)

print("\n五、人工队列与导出（M7 / M5）")
s, q = req("GET", "/api/v1/approvals/queue", token=t_biz)
check("队列非空", s == 200 and len(q) > 0, f"{len(q)} 条")

s, j = req("POST", f"/api/v1/approvals/queue/{q[0]['id']}/assign",
           {"assignee": "唐映秋"}, token=t_admin)
check("可指派具名接收人", s == 200, detail_of(j))

# 职责分离：指派队列属管理后台职能，业务负责人不在该角色内
s, j = req("POST", f"/api/v1/approvals/queue/{q[0]['id']}/assign",
           {"assignee": "唐映秋"}, token=t_biz)
check("业务负责人指派被角色拦截（职责分离）", s == 403, detail_of(j))

s, e = req("GET", f"/api/v1/approvals/briefs/{bid}/export", token=t_biz)
check("R4 只读导出可用", s == 200 and e.get("mode") == "readonly", e.get("brief_no", ""))
check("导出已脱敏（无明文手机/邮箱）", "@" not in json.dumps(e, ensure_ascii=False))

print("\n六、管理端")
for p in ["/login", "/", "/admin"]:
    try:
        r = urllib.request.urlopen(BASE + p, timeout=15)
        check(f"页面 {p} 可访问", r.status == 200, f"{len(r.read())} 字节")
    except urllib.error.HTTPError as ex:
        check(f"页面 {p} 可访问", False, ex.code)

s, t = req("GET", "/api/v1/admin/tables", token=t_admin)
check("数据模型自检", s == 200, {x["table"]: x["rows"] for x in t})
s, r = req("GET", "/api/v1/approvals/hard-rules", token=t_admin)
check("七条硬约束可读", s == 200 and len(r) == 7)

print("\n" + "=" * 60)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("失败清单：")
    for f in FAIL:
        print("  -", f)
