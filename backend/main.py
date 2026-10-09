"""Retrenchment Laptop Tracker backend (FastAPI on OceanBase / MySQL wire).

- GET /health                      readiness probe
- GET /api/me                      signed-in user's email (Google SSO header)
- GET/POST /api/departments        department tabs
- GET/POST /api/laptops            list / add laptops
- PUT /api/laptops/{id}            edit one laptop (optimistic lock on `version`)
- POST /api/import                 merge Excel rows (dry run first, then apply)

All DDL lives in resources/db/migration/. asyncmy with %s placeholders.
"""
import os
import re
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse

import asyncmy
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

_pool = None


def _dsn() -> dict:
    u = urlparse(os.environ["DATABASE_URL"])
    return {
        "host": u.hostname,
        "port": u.port or 2881,
        "user": unquote(u.username or ""),
        "password": unquote(u.password or ""),
        "db": (u.path or "/").lstrip("/"),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pool
    if os.getenv("DATABASE_URL"):
        _pool = await asyncmy.create_pool(**_dsn(), autocommit=True, charset="utf8mb4")
    yield
    if _pool is not None:
        _pool.close()
        await _pool.wait_closed()


app = FastAPI(title="Retrenchment Laptop Tracker", lifespan=lifespan)

# Laptop fields: API (camelCase) name -> DB column.
FIELDS = {
    "employeeName": "employee_name",
    "employeeId": "employee_id",
    "subDept": "sub_dept",
    "manufacturer": "manufacturer",
    "model": "model",
    "hostname": "hostname",
    "inventoryTag": "inventory_tag",
    "serial": "serial",
    "returnStatus": "return_status",
    "dateReturned": "date_returned",
    "snipeit": "snipeit",
    "win11": "win11",
    "win11Version": "win11_version",
    "remarks": "remarks",
}
SELECT_COLS = "id, dept_id, " + ", ".join(FIELDS.values()) + ", sort_order, updated_by, updated_at, version"


def _need_db():
    if _pool is None:
        raise HTTPException(503, "The database isn't connected yet. Try again in a minute.")


def _who(request: Request) -> str:
    return request.headers.get("x-forwarded-email") or ""


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


# ---------- models ----------
class Health(BaseModel):
    status: str


class Me(BaseModel):
    email: str | None


class Department(BaseModel):
    id: str
    name: str
    order: int


class DepartmentList(BaseModel):
    departments: list[Department]


class DepartmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)


class LaptopData(BaseModel):
    employeeName: str = ""
    employeeId: str = ""
    subDept: str = ""
    manufacturer: str = ""
    model: str = ""
    hostname: str = ""
    inventoryTag: str = ""
    serial: str = ""
    returnStatus: str = "Pending"
    dateReturned: str = ""
    snipeit: str = ""
    win11: str = ""
    win11Version: str = ""
    remarks: str = ""


class Laptop(LaptopData):
    id: str
    dept: str
    order: int
    updatedBy: str
    updatedAt: str
    version: int


class LaptopList(BaseModel):
    laptops: list[Laptop]


class LaptopCreate(LaptopData):
    dept: str


class LaptopUpdate(LaptopData):
    version: int


def _row_to_laptop(r) -> dict:
    d = {"id": str(r[0]), "dept": r[1]}
    for i, k in enumerate(FIELDS):
        d[k] = r[2 + i] or ""
    n = 2 + len(FIELDS)
    d["order"] = r[n] or 0
    d["updatedBy"] = r[n + 1] or ""
    d["updatedAt"] = (r[n + 2].isoformat() + "Z") if r[n + 2] else ""
    d["version"] = r[n + 3]
    return d


def _clean(data: LaptopData) -> dict:
    out = {k: (getattr(data, k) or "").strip() for k in FIELDS}
    if out["returnStatus"] not in ("Returned", "Pending"):
        out["returnStatus"] = "Pending"
    return out


async def _fetch_laptop(cur, laptop_id: int) -> dict | None:
    await cur.execute(f"SELECT {SELECT_COLS} FROM laptops WHERE id=%s", (laptop_id,))
    r = await cur.fetchone()
    return _row_to_laptop(r) if r else None


# ---------- routes ----------
@app.get("/health", response_model=Health)
def health():
    return {"status": "ok"}


@app.get("/api/me", response_model=Me)
def me(request: Request):
    return {"email": _who(request) or None}


@app.get("/api/departments", response_model=DepartmentList)
async def list_departments():
    _need_db()
    async with _pool.acquire() as conn, conn.cursor() as cur:
        await cur.execute("SELECT id, name, sort_order FROM departments ORDER BY sort_order, name")
        rows = await cur.fetchall()
    return {"departments": [{"id": r[0], "name": r[1], "order": r[2]} for r in rows]}


@app.post("/api/departments", status_code=201, response_model=Department)
async def create_department(body: DepartmentIn, request: Request):
    _need_db()
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Type a name for the new tab.")
    async with _pool.acquire() as conn, conn.cursor() as cur:
        await cur.execute("SELECT name, sort_order FROM departments")
        rows = await cur.fetchall()
        if any(r[0].lower() == name.lower() for r in rows):
            raise HTTPException(409, "A tab with that name already exists.")
        order = max([r[1] for r in rows] + [0]) + 1
        slug = (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "dept")[:50] + "-" + secrets.token_hex(2)
        await cur.execute(
            "INSERT INTO departments (id, name, sort_order, created_by) VALUES (%s, %s, %s, %s)",
            (slug, name, order, _who(request)),
        )
    return {"id": slug, "name": name, "order": order}


@app.get("/api/laptops", response_model=LaptopList)
async def list_laptops():
    _need_db()
    async with _pool.acquire() as conn, conn.cursor() as cur:
        await cur.execute(f"SELECT {SELECT_COLS} FROM laptops ORDER BY dept_id, sort_order, id")
        rows = await cur.fetchall()
    return {"laptops": [_row_to_laptop(r) for r in rows]}


@app.post("/api/laptops", status_code=201, response_model=Laptop)
async def create_laptop(body: LaptopCreate, request: Request):
    _need_db()
    data = _clean(body)
    if not data["serial"] and not data["hostname"]:
        raise HTTPException(400, "Enter a serial number or hostname so the laptop can be found later.")
    async with _pool.acquire() as conn, conn.cursor() as cur:
        await cur.execute("SELECT COUNT(*) FROM departments WHERE id=%s", (body.dept,))
        if (await cur.fetchone())[0] == 0:
            raise HTTPException(404, "That department tab no longer exists.")
        await cur.execute("SELECT COALESCE(MAX(sort_order), 0) FROM laptops WHERE dept_id=%s", (body.dept,))
        order = (await cur.fetchone())[0] + 1
        cols = ["dept_id"] + list(FIELDS.values()) + ["sort_order", "updated_by", "updated_at"]
        vals = [body.dept] + [data[k] for k in FIELDS] + [order, _who(request), _now()]
        await cur.execute(
            f"INSERT INTO laptops ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})", vals
        )
        new_id = cur.lastrowid
        return await _fetch_laptop(cur, new_id)


@app.put("/api/laptops/{laptop_id}", response_model=Laptop)
async def update_laptop(laptop_id: int, body: LaptopUpdate, request: Request):
    _need_db()
    data = _clean(body)
    if not data["serial"] and not data["hostname"]:
        raise HTTPException(400, "Enter a serial number or hostname so the laptop can be found later.")
    sets = ", ".join(f"{c}=%s" for c in FIELDS.values())
    async with _pool.acquire() as conn, conn.cursor() as cur:
        await cur.execute(
            f"UPDATE laptops SET {sets}, updated_by=%s, updated_at=%s, version=version+1 WHERE id=%s AND version=%s",
            [data[k] for k in FIELDS] + [_who(request), _now(), laptop_id, body.version],
        )
        if cur.rowcount == 0:
            current = await _fetch_laptop(cur, laptop_id)
            if current is None:
                raise HTTPException(404, "This laptop no longer exists.")
            raise HTTPException(409, "Someone else changed this laptop while you were editing. Close it and open it again to see their changes.")
        return await _fetch_laptop(cur, laptop_id)


# ---------- Excel import ----------
MAN = {"DELL": "Dell", "LENOVO": "Lenovo", "ASUS": "Asus", "HUAWEI": "Huawei", "HP": "HP", "ACER": "Acer", "APPLE": "Apple"}
ENUMS = {
    "returnStatus": ["Returned", "Pending"],
    "snipeit": ["Ready to Deploy", "Deploy", "In Checking", "Faulty"],
    "win11": ["Yes", "No", "N/a"],
}


def _key(v: str) -> str:
    # Serial match key: ignore spaces/case and treat letter O/I like digits 0/1 (common typos).
    return re.sub(r"\s+", "", v or "").upper().replace("O", "0").replace("I", "1")


def _norm_row(raw: dict) -> dict:
    r = {k: re.sub(r"\s+", " ", str(raw.get(k) or "").replace("\t", "")).strip() for k in FIELDS}
    m = r["manufacturer"].upper()
    if m.startswith("MY"):
        m = m[2:]
    m = m.strip(" -")
    r["manufacturer"] = MAN.get(m, m.title() if m else "")
    for k, allowed in ENUMS.items():
        for a in allowed:
            if r[k].lower() == a.lower():
                r[k] = a
    if r["serial"]:
        r["serial"] = r["serial"].replace(" ", "")
    return r


class ImportSheet(BaseModel):
    department: str
    rows: list[dict]


class ImportIn(BaseModel):
    dryRun: bool = True
    sheets: list[ImportSheet]


class ImportChange(BaseModel):
    department: str
    laptop: str
    field: str
    old: str
    new: str


class ImportNew(BaseModel):
    department: str
    laptop: str
    returnStatus: str


class ImportConflict(BaseModel):
    department: str
    laptop: str
    field: str
    kept: str
    other: str


class ImportResult(BaseModel):
    applied: bool
    updatedLaptops: int
    newLaptops: int
    changes: list[ImportChange]
    added: list[ImportNew]
    conflicts: list[ImportConflict]
    skippedRows: int


@app.post("/api/import", response_model=ImportResult)
async def import_rows(body: ImportIn, request: Request):
    _need_db()
    who = _who(request)
    changes, added, conflicts = [], [], []
    updates: dict[int, tuple[dict, int]] = {}
    inserts: list[tuple[str, dict]] = []
    skipped = 0
    async with _pool.acquire() as conn, conn.cursor() as cur:
        await cur.execute("SELECT id, name FROM departments")
        depts = {r[0]: r[1] for r in await cur.fetchall()}
        await cur.execute(f"SELECT {SELECT_COLS} FROM laptops")
        existing = [_row_to_laptop(r) for r in await cur.fetchall()]

        for sheet in body.sheets:
            if sheet.department not in depts:
                raise HTTPException(400, f"Unknown department tab: {sheet.department}")
            dname = depts[sheet.department]
            mine = [l for l in existing if l["dept"] == sheet.department]
            by_serial = {_key(l["serial"]): l for l in mine if l["serial"]}
            by_host = {_key(l["hostname"]): l for l in mine if l["hostname"]}
            merged: dict[str, dict] = {}
            order: list[str] = []
            for raw in sheet.rows:
                r = _norm_row(raw)
                if not r["serial"] and not r["hostname"]:
                    skipped += 1
                    continue
                k = "S:" + _key(r["serial"]) if r["serial"] else "H:" + _key(r["hostname"])
                if not r["serial"] and _key(r["hostname"]) in by_host and by_host[_key(r["hostname"])]["serial"]:
                    k = "S:" + _key(by_host[_key(r["hostname"])]["serial"])
                if k in merged:
                    # Duplicate row in the file: the later (newer) row wins.
                    prev = merged[k]
                    for f in FIELDS:
                        if r[f] and prev[f] and r[f] != prev[f] and f not in ("remarks", "serial"):
                            conflicts.append({"department": dname, "laptop": r["serial"] or r["hostname"], "field": f, "kept": r[f], "other": prev[f]})
                        if r[f]:
                            prev[f] = r[f]
                    continue
                merged[k] = r
                order.append(k)

            for k in order:
                r = merged[k]
                cur_l = by_serial.get(k[2:]) if k.startswith("S:") else by_host.get(k[2:])
                label = r["serial"] or r["hostname"]
                if cur_l is None:
                    if not r["returnStatus"]:
                        r["returnStatus"] = "Pending"
                    inserts.append((sheet.department, r))
                    added.append({"department": dname, "laptop": label, "returnStatus": r["returnStatus"]})
                    continue
                upd = {}
                for f in FIELDS:
                    v, old = r[f], cur_l.get(f, "")
                    if not v or v == old or f == "serial":
                        continue
                    if f == "remarks" and old and v in old:
                        continue
                    if f == "returnStatus" and v == "Pending" and old == "Returned":
                        continue
                    upd[f] = v
                    changes.append({"department": dname, "laptop": cur_l["serial"] or label, "field": f, "old": old, "new": v})
                if upd:
                    updates[int(cur_l["id"])] = (upd, cur_l["version"])

        if not body.dryRun:
            now = _now()
            for lid, (upd, ver) in updates.items():
                sets = ", ".join(f"{FIELDS[f]}=%s" for f in upd)
                await cur.execute(
                    f"UPDATE laptops SET {sets}, updated_by=%s, updated_at=%s, version=version+1 WHERE id=%s AND version=%s",
                    list(upd.values()) + [who, now, lid, ver],
                )
                if cur.rowcount == 0:
                    raise HTTPException(409, "Someone changed a laptop while the import was running. Run the import again.")
            for dept, r in inserts:
                await cur.execute("SELECT COALESCE(MAX(sort_order), 0) FROM laptops WHERE dept_id=%s", (dept,))
                o = (await cur.fetchone())[0] + 1
                cols = ["dept_id"] + list(FIELDS.values()) + ["sort_order", "updated_by", "updated_at"]
                await cur.execute(
                    f"INSERT INTO laptops ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                    [dept] + [r[k] for k in FIELDS] + [o, who, now],
                )

    return {
        "applied": not body.dryRun,
        "updatedLaptops": len(updates),
        "newLaptops": len(inserts),
        "changes": changes,
        "added": added,
        "conflicts": conflicts,
        "skippedRows": skipped,
    }
