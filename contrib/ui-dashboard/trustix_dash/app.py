from trustix_api import api_pb2
from fastapi.staticfiles import StaticFiles
from fastapi.templating import (
    Jinja2Templates,
)
from fastapi.responses import (
    RedirectResponse,
    HTMLResponse,
)
from fastapi import (
    FastAPI,
    Request,
    Form,
)
from typing import (
    Optional,
    Dict,
    List,
)
from trustix_dash import (
    template_lib,
    on_startup,
    on_shutdown,
)
from collections import OrderedDict
import urllib.parse
import requests
import tempfile
import asyncio
import os.path
import codecs
import shlex
import json

from trustix_dash.api import (
    get_derivation_output_results_unique,
    get_derivation_reproducibility,
    get_attr_reproducibility,
    search_derivations,
    suggest_attrs,
)

from trustix_dash.proto import (
    get_combined_rpc,
)

from trustix_dash.conf import settings


SCRIPT_DIR = os.path.dirname(__file__)


app = FastAPI()
app.mount(
    "/static", StaticFiles(directory=os.path.join(SCRIPT_DIR, "static")), name="static"
)


templates = Jinja2Templates(directory=os.path.join(SCRIPT_DIR, "templates"))
templates.env.globals["drv_url_quote"] = template_lib.drv_url_quote
templates.env.globals["json_render"] = template_lib.json_render
templates.env.globals["url_reverse"] = app.url_path_for


@app.on_event("startup")
async def startup_event():
    await on_startup()


@app.on_event("shutdown")
async def shutdown_event():
    await on_shutdown()


def make_context(
    request: Request,
    title: str = "",
    extra: Optional[Dict] = None,
) -> Dict:

    ctx = {
        "request": request,
        "title": "Trustix R13Y" + (" ".join((" - ", title)) if title else ""),
        "drv_placeholder": settings.placeholder_attr,
    }

    if extra:
        ctx.update(extra)

    return ctx


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    attrs = settings.default_attrs
    ctx = make_context(
        request,
        extra={
            "attr_stats": OrderedDict(
                zip(
                    attrs,
                    await asyncio.gather(
                        *[get_attr_reproducibility(attr) for attr in attrs]
                    ),
                )
            ),
        },
    )
    return templates.TemplateResponse("index.jinja2", ctx)


@app.get("/attr/{attr}", response_class=HTMLResponse)
async def attr(request: Request, attr: str):
    ctx = make_context(
        request,
        extra={
            "attr": attr,
            "attr_data": await get_attr_reproducibility(attr),
        },
    )
    return templates.TemplateResponse("attr.jinja2", ctx)


@app.get("/drv/{drv_path}", response_class=HTMLResponse)
async def drv(request: Request, drv_path: str):
    ctx = make_context(
        request,
        extra={
            "data": await get_derivation_reproducibility(
                urllib.parse.unquote(drv_path)
            ),
        },
    )
    return templates.TemplateResponse("drv.jinja2", ctx)


@app.post("/search_form/")
async def search_form(request: Request, term: str = Form(...)):
    return RedirectResponse(app.url_path_for("search", term=term))


@app.post("/search/{term}")
async def search(request: Request, term: str):

    derivations_by_attr = search_derivations(term)

    ctx = make_context(
        request,
        extra={
            "derivations_by_attr": derivations_by_attr,
        },
    )

    return templates.TemplateResponse("search.jinja2", ctx)


@app.get("/suggest/{attr_prefix}", response_model=List[str])
async def suggest(request: Request, attr_prefix: str):
    return suggest_attrs(attr_prefix)


@app.post("/diff_form/", response_class=HTMLResponse)
async def diff_form(request: Request, output_hash: List[str] = Form(...)):

    if len(output_hash) < 1:
        raise ValueError("Need at least 2 entries to diff")
    if len(output_hash) > 2:
        raise ValueError("Received more than 2 entries to diff")

    return RedirectResponse(
        app.url_path_for(
            "diff", output_hash_1_hex=output_hash[0], output_hash_2_hex=output_hash[1]
        )
    )


@app.get("/diff/{output_hash_1_hex}/{output_hash_2_hex}", response_class=HTMLResponse)
@app.post("/diff/{output_hash_1_hex}/{output_hash_2_hex}", response_class=HTMLResponse)
async def diff(request: Request, output_hash_1_hex: str, output_hash_2_hex: str):

    output_hash_1 = codecs.decode(output_hash_1_hex, "hex")  # type: ignore
    output_hash_2 = codecs.decode(output_hash_2_hex, "hex")  # type: ignore

    result1, result2 = await get_derivation_output_results_unique(
        output_hash_1, output_hash_2
    )

    # Uvloop has a nasty bug https://github.com/MagicStack/uvloop/issues/317
    # To work around this we run the fetching/unpacking in a separate blocking thread
    def fetch_unpack_nar(url, location):
        import subprocess

        loc_base = os.path.basename(location)
        loc_dir = os.path.dirname(location)

        try:
            os.mkdir(loc_dir)
        except FileExistsError:
            pass

        with requests.get(url, stream=True) as r:
            r.raise_for_status()
            p = subprocess.Popen(
                ["nix-nar-unpack", loc_base], stdin=subprocess.PIPE, cwd=loc_dir
            )
            for chunk in r.iter_content(chunk_size=512):
                p.stdin.write(chunk)
            p.stdin.close()
            p.wait(timeout=0.5)

        # Ensure correct mtime
        for subl in (
            (os.path.join(dirpath, f) for f in (dirnames + filenames))
            for (dirpath, dirnames, filenames) in os.walk(location)
        ):
            for path in subl:
                os.utime(path, (1, 1))
        os.utime(location, (1, 1))

    async def process_result(result, tmpdir, outbase) -> str:
        # Fetch narinfo
        narinfo = json.loads(
            (await get_combined_rpc().GetValue(api_pb2.ValueRequest(Digest=result.output_hash))).Value  # type: ignore
        )
        nar_hash = narinfo["narHash"].split(":")[-1]

        # Get store prefix
        output = await result.output

        store_base = output.store_path.split("/")[-1]
        store_prefix = store_base.split("-")[0]

        unpack_dir = os.path.join(tmpdir, store_base, outbase)
        nar_url = "/".join((settings.binary_cache_proxy, "nar", store_prefix, nar_hash))

        await asyncio.get_running_loop().run_in_executor(
            None, fetch_unpack_nar, nar_url, unpack_dir
        )

        return unpack_dir

    # TODO: Async tempfile
    with tempfile.TemporaryDirectory(prefix="trustix-ui-dash-diff") as tmpdir:
        dir_a, dir_b = await asyncio.gather(
            process_result(result1, tmpdir, "A"),
            process_result(result2, tmpdir, "B"),
        )

        dir_a_rel = os.path.join(os.path.basename(os.path.dirname(dir_a)), "A")
        dir_b_rel = os.path.join(os.path.basename(os.path.dirname(dir_b)), "B")

        proc = await asyncio.create_subprocess_shell(
            shlex.join(["diffoscope", "--html", "-", dir_a_rel, dir_b_rel]),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=tmpdir,
        )
        stdout, stderr = await proc.communicate()

    # Diffoscope returns non-zero on paths that have a diff
    # Instead use stderr as a heurestic if the call went well or not
    if stderr:
        raise ValueError(stderr)

    return stdout
