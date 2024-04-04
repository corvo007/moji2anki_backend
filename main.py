import os
import uuid
from typing import Dict

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import HttpUrl
from fastapi.middleware.cors import CORSMiddleware


from core import deck_temp_dir, generate_anki_cards, purge_cache, voice_temp_dir
from exception import *

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins="*",
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

# 存储进度日志，其中每项任务是一个列表，记录了该任务的状态变更历史
progress_logs: Dict[str, list] = {}


async def remove_file(path: str):
    os.remove(path)


# 用于更新进度日志的辅助函数
def update_progress_log(task_id: str, status: str):
    if task_id in progress_logs:
        progress_logs[task_id].append(status)
    else:
        progress_logs[task_id] = [status]


@app.exception_handler(Exception)
async def custom_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=jsonable_encoder({"detail": str(exc), "status_code": 500}),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=jsonable_encoder(
            {"detail": f"params error:{exc.errors()}", "status_code": 422}
        ),
    )


@app.exception_handler(BaseError)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=jsonable_encoder({"detail": str(exc), "status_code": -1}),
    )


# API端点，用于创建APKG文件
@app.get("/create-apkg/", response_class=JSONResponse)
async def create_apkg(background_tasks: BackgroundTasks, url: HttpUrl = Query(...)):
    task_id = str(uuid.uuid4())
    update_progress_log(task_id, "Task created")

    # 调用创建APKG后台任务的函数
    background_tasks.add_task(generate_anki_cards, url, task_id, update_progress_log)

    return {"task_id": task_id}


# API端点，用于查询进度日志
@app.get("/progress-log/{task_id}", response_class=JSONResponse)
def get_progress_log(task_id: str):
    progress_log = progress_logs.get(task_id, None)
    if progress_log is None:
        raise NotFound("Task not found")
    return {"task_id": task_id, "progress_log": progress_log}


@app.get("/download-apkg/{task_id}")
async def download_apkg(task_id: str):
    file_path = os.path.join(deck_temp_dir, f"{task_id}.apkg")
    if os.path.exists(file_path):
        return FileResponse(
            path=file_path,
            filename=f"{task_id}.apkg",
            media_type="application/octet-stream",
        )
    else:
        raise NotFound("File not found")


@app.on_event("shutdown")
async def shutdown_event():
    await purge_cache()
