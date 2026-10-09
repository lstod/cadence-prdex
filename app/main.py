from fastapi import FastAPI, Request
from app.routes.auth import router as auth_router
from app.routes.hooks import slack_router, github_router
from app.routes.runs import runs_router
from app.routes.workflows import workflows_router

app = FastAPI(title="cadence")


@app.middleware("http")
async def add_rate_limit_headers(request: Request, call_next):
    # api_key_auth stores the headers once a key passes authentication.
    response = await call_next(request)
    headers = getattr(request.state, "rate_limit_headers", None)
    if headers is not None:
        for name, value in headers.items():
            response.headers[name] = value
    return response


app.include_router(auth_router)
app.include_router(slack_router)
app.include_router(github_router)
app.include_router(runs_router)
app.include_router(workflows_router)


@app.get("/health")
def health():
    return {"ok": True}
