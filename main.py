import os
import secrets
import time
from contextlib import asynccontextmanager, contextmanager
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

BASE = Path(__file__).parent

# ---- Konfigurácia z Environment Variables (na Renderi: Environment) ----
DATABASE_URL = os.environ["DATABASE_URL"]          # Internal Database URL z Render Postgresu
SESSION_SECRET = os.environ["SESSION_SECRET"]      # dlhý náhodný reťazec
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "1") == "1"   # lokálne nastav COOKIE_SECURE=0

# názov, cena, kategória, kusy
SKLAD_POVODNY = [
    ("jablko", Decimal("1.00"), "ovocie", 2),
    ("ananas", Decimal("1.30"), "ovocie", 500),
    ("mrkva", Decimal("0.20"), "zelenina", 800),
    ("kitkat", Decimal("0.80"), "sladkosti", 300),
    ("kinder", Decimal("1.50"), "sladkosti", 400),
]


# ------------------------------------------------------------------ databáza
@contextmanager
def db():
    # commit pri úspechu, rollback pri výnimke, spojenie sa vždy zatvorí
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        yield conn


def naplnit_sklad(c):
    with c.cursor() as cur:
        cur.executemany(
            "INSERT INTO produkty (nazov, cena, kategoria, kusy) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (nazov) DO NOTHING",
            SKLAD_POVODNY,
        )

def init_db():
    with db() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS produkty (
                   nazov     TEXT PRIMARY KEY,
                   cena      NUMERIC(10, 2) NOT NULL CHECK (cena >= 0),
                   kategoria TEXT NOT NULL,
                   kusy      BIGINT NOT NULL CHECK (kusy >= 0)
               )"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS kosik (
                   nazov TEXT PRIMARY KEY REFERENCES produkty(nazov) ON DELETE CASCADE,
                   pocet INTEGER NOT NULL CHECK (pocet > 0)
               )"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS stav (
                   id    INTEGER PRIMARY KEY CHECK (id = 1),
                   zlava BOOLEAN NOT NULL DEFAULT FALSE
               )"""
        )
        c.execute("INSERT INTO stav (id, zlava) VALUES (1, FALSE) ON CONFLICT DO NOTHING")
        if c.execute("SELECT COUNT(*) AS n FROM produkty").fetchone()["n"] == 0:
            naplnit_sklad(c)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


# ---------------------------------------------------------------- aplikácia
# docs/openapi vypnuté, aby sa verejne nevypisovali admin endpointy
app = FastAPI(title="Obchod", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie="sid",
    https_only=COOKIE_SECURE,   # príznak Secure
    same_site="strict",         # ochrana proti CSRF
    max_age=8 * 3600,
)  # cookie je v Starlette vždy HttpOnly


@app.middleware("http")
async def bezpecnostne_hlavicky(request: Request, call_next):
    r = await call_next(request)
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["X-Frame-Options"] = "DENY"
    r.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if COOKIE_SECURE:
        r.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.url.path.startswith(("/api", "/admin")):
        r.headers["Cache-Control"] = "no-store"
    return r


app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


# ----------------------------------------------------------------- modely
class PridanieKusov(BaseModel):
    pocet: int = Field(gt=0, le=100_000)


class Prihlasenie(BaseModel):
    heslo: str = Field(min_length=1, max_length=128)


# ------------------------------------------------------------ autentifikácia
def require_admin(request: Request):
    if not request.session.get("admin"):
        raise HTTPException(status_code=401, detail="Nie si prihlásený")


_pokusy: dict[str, list[float]] = {}   # jednoduchý rate limit prihlásenia (v pamäti)
MAX_POKUSOV, OKNO_SEKUND = 5, 300


def klient(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")


@app.post("/api/login")
def login(data: Prihlasenie, request: Request):
    if not ADMIN_PASSWORD:
        raise HTTPException(status_code=503, detail="Admin nie je nastavený")
    kluc, teraz = klient(request), time.time()
    pokusy = [t for t in _pokusy.get(kluc, []) if teraz - t < OKNO_SEKUND]
    if len(pokusy) >= MAX_POKUSOV:
        raise HTTPException(status_code=429, detail="Príliš veľa pokusov, skús neskôr")
    if not secrets.compare_digest(data.heslo.encode(), ADMIN_PASSWORD.encode()):
        pokusy.append(teraz)
        _pokusy[kluc] = pokusy
        raise HTTPException(status_code=401, detail="Nesprávne heslo")
    _pokusy.pop(kluc, None)
    request.session.clear()
    request.session["admin"] = True
    return {"sprava": "Prihlásený"}


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"sprava": "Odhlásený"}


# -------------------------------------------------------------------- pomocné
def vypis_kosika(c):
    rows = c.execute(
        """SELECT p.nazov, p.cena, p.kategoria, p.kusy AS na_sklade, k.pocet
           FROM kosik k JOIN produkty p ON p.nazov = k.nazov
           ORDER BY p.nazov"""
    ).fetchall()
    zlava = c.execute("SELECT zlava FROM stav WHERE id = 1").fetchone()["zlava"]
    cena = sum((r["cena"] * r["pocet"] for r in rows), Decimal("0"))
    if zlava:
        cena = cena * Decimal("0.90")
    return {
        "polozky": [
            {
                "nazov": r["nazov"],
                "cena": float(r["cena"]),
                "kategoria": r["kategoria"],
                "pocet": r["pocet"],
                "na_sklade": r["na_sklade"],
            }
            for r in rows
        ],
        "zlava": zlava,
        "celkova_cena": float(cena.quantize(Decimal("0.01"), ROUND_HALF_UP)),
    }


# --------------------------------------------------------------------- stránky
@app.get("/")
def index():
    return FileResponse(BASE / "index.html")


@app.get("/admin")
def admin(request: Request):
    if not request.session.get("admin"):
        return RedirectResponse("/", status_code=303)
    return FileResponse(BASE / "index.admin.html")


# ------------------------------------------------------------------ verejné API
@app.get("/api/sklad")
def get_sklad():
    with db() as c:
        rows = c.execute(
            "SELECT nazov, cena, kategoria, kusy FROM produkty ORDER BY nazov"
        ).fetchall()
    return [
        {"nazov": r["nazov"], "cena": float(r["cena"]), "kategoria": r["kategoria"], "kusy": r["kusy"]}
        for r in rows
    ]


@app.get("/api/kosik")
def get_kosik():
    with db() as c:
        return vypis_kosika(c)


@app.post("/api/kosik/{polozka}")
def pridaj_do_kosika(polozka: str):
    polozka = polozka.lower().strip()
    with db() as c:
        # atomicky: zníž sklad len ak je dostupný (žiadne preprodanie pri súbehu)
        row = c.execute(
            "UPDATE produkty SET kusy = kusy - 1 WHERE nazov = %s AND kusy > 0 "
            "RETURNING cena, kusy",
            (polozka,),
        ).fetchone()
        if not row:
            existuje = c.execute("SELECT 1 FROM produkty WHERE nazov = %s", (polozka,)).fetchone()
            if not existuje:
                raise HTTPException(status_code=404, detail="Taká položka v sklade nie je")
            raise HTTPException(status_code=400, detail="Položka je vypredaná")
        c.execute(
            "INSERT INTO kosik (nazov, pocet) VALUES (%s, 1) "
            "ON CONFLICT (nazov) DO UPDATE SET pocet = kosik.pocet + 1",
            (polozka,),
        )
        return {
            "sprava": f"Pridané do košíka: {polozka} ({row['cena']} €). Na sklade zostáva {row['kusy']} ks.",
            **vypis_kosika(c),
        }


@app.post("/api/zlava")
def pouzi_kupon():
    with db() as c:
        if c.execute("SELECT zlava FROM stav WHERE id = 1").fetchone()["zlava"]:
            raise HTTPException(status_code=400, detail="Kupón je už použitý")
        if not c.execute("SELECT 1 FROM kosik LIMIT 1").fetchone():
            raise HTTPException(status_code=400, detail="Košík je prázdny")
        c.execute("UPDATE stav SET zlava = TRUE WHERE id = 1")
        return {"sprava": "Super, 10 % zľava bola aplikovaná.", **vypis_kosika(c)}


@app.post("/api/vymaz-kosik")
def vymaz_kosik():
    """Verejný reset košíka: vráti kusy na sklad a zruší kupón."""
    with db() as c:
        c.execute(
            "UPDATE produkty p SET kusy = p.kusy + k.pocet FROM kosik k WHERE k.nazov = p.nazov"
        )
        c.execute("DELETE FROM kosik")
        c.execute("UPDATE stav SET zlava = FALSE WHERE id = 1")
        return {"sprava": "Košík bol vyprázdnený.", **vypis_kosika(c)}


# ------------------------------------------------------------------ admin API
@app.post("/api/sklad/{polozka}/pridaj", dependencies=[Depends(require_admin)])
def pridaj_kusy(polozka: str, data: PridanieKusov):
    polozka = polozka.lower().strip()
    with db() as c:
        row = c.execute(
            "UPDATE produkty SET kusy = kusy + %s WHERE nazov = %s RETURNING kusy",
            (data.pocet, polozka),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Taká položka v sklade nie je")
    return {
        "sprava": f"Pridaných {data.pocet} ks: {polozka}. Na sklade je teraz {row['kusy']} ks.",
        "nazov": polozka,
        "kusy": row["kusy"],
    }


@app.post("/api/reset", dependencies=[Depends(require_admin)])
def reset():
    with db() as c:
        c.execute("DELETE FROM kosik")
        c.execute("DELETE FROM produkty")
        naplnit_sklad(c)
        c.execute("UPDATE stav SET zlava = FALSE WHERE id = 1")
        return {"sprava": "Obchod bol resetovaný.", **vypis_kosika(c)}
