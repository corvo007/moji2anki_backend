import asyncio
import os.path
import random
import re
import time
from typing import Union
from urllib.parse import urlparse

import genanki
import httpx

from const import *
from exception import *
from log import logger

card_model = genanki.Model(**MODELS)
card_deck = genanki.Deck(**DECK)

regex_pattern = re.compile(r"\[(.*?)\]")

voice_temp_dir = os.path.join(os.path.dirname(__file__), "voice_temp")
deck_temp_dir = os.path.join(os.path.dirname(__file__), "deck_temp")
font_dir = os.path.join(os.path.dirname(__file__), "fonts")

os.makedirs(voice_temp_dir, exist_ok=True)
os.makedirs(deck_temp_dir, exist_ok=True)


def is_kana(word):
    # 对于字符串中的每个字符
    for char in word:
        # 如果字符是假名，跳过继续
        if "\u3040" <= char <= "\u309F" or "\u30A0" <= char <= "\u30FF":
            continue
        # 如果不是假名，返回False
        else:
            return False
    # 如果所有字符都是假名，返回True
    return True


async def get_data(url: str, body: dict, extra_headers=None) -> dict:
    if extra_headers is None:
        extra_headers = {}
    async with httpx.AsyncClient(
        headers={**headers, **extra_headers}, timeout=10
    ) as client:
        query_data = {**body, **auth}
        response = await client.post(url, json=query_data)
    if response.status_code != 200:
        raise NetworkError(f"网络异常\nURL:{response.url}\n{response.text}")
    response_dict = response.json()
    if response_dict["result"]["code"] == 100000006:
        raise UnauthorizedError(f"词单不存在或无权访问\nURL:{response.url}")
    elif response_dict["result"]["code"] == 200:
        return response_dict
    else:
        raise DataError(f"数据无效\nURL:{response.url}\n{response.text}")


async def get_word_id(word_list: list) -> list:
    word_id_list = []
    for word in word_list:
        if word["targetType"] == 102:
            word_id_list.append(word["targetId"])
    return word_id_list


async def get_word_detail_batch(word_id_list: list) -> list:
    query_body = {"itemsJson": [], "skipAccessories": False}
    for x in word_id_list:
        query_body["itemsJson"].append({"objectId": x})
    word_details = (
        await get_data(
            WORD_DETAIL_BATCH_API,
            query_body,
            {
                "Content-Type": "text/plain",
            },
        )
    )["result"]["result"]
    return word_details


async def get_word_detail(word_id: str) -> dict:
    query_body = {"itemsJson": [{"objectId": word_id, "lfd": 0}]}
    word_detail = (
        await get_data(
            WORD_DETAIL_API,
            query_body,
            {
                "Content-Type": "text/plain",
            },
        )
    )["result"]

    return word_detail


async def get_word_voice(word_id: str) -> Union[bool, str]:
    word_voice = (
        await get_data(
            WORD_VOICE_API,
            {
                "tarId": word_id,
                "tarType": 102,
                "voiceId": "f002",
            },
            {
                "Content-Type": "text/plain",
            },
        )
    )["result"]["result"]

    # 最大重试次数
    max_retries = 1
    # 当前重试次数
    attempts = 0

    while attempts <= max_retries:
        try:
            # 使用 httpx.Client 下载音频并自动处理会话
            with httpx.Client() as client:
                response = client.get(word_voice["url"], timeout=10)
                # 确保请求成功
                response.raise_for_status()
                audio_content = response.content
            # 成功获取到音频文件内容后，退出循环
            break
        except Exception as e:
            # 打印出错信息
            logger.exception(f"An HTTP error occurred")
            attempts += 1
            if attempts > max_retries:
                logger.exception(f"An unexpected error has occurred")
            else:
                logger.warning(f"Retrying... Attempt {attempts} of {max_retries}")

    if audio_content:
        with open(os.path.join(voice_temp_dir, f"{word_id}.mp3"), "wb") as f:
            f.write(audio_content)
    else:
        return False

    return True


async def generate_word_card_batch(word_id_list: list, source: str = ""):
    download_failed_voice = []
    for n, word in enumerate(word_id_list, 1):
        logger.info(f"正在生成第{n}/{len(word_id_list)}张单词卡片")
        download_failed_voice = await generate_word_card(word, source)

    return download_failed_voice


async def generate_word_card(word_id: str, source: str):
    download_failed_voice = ""
    word = await get_word_detail(word_id)
    definition = ""
    await asyncio.sleep(random.randrange(15, 50) / 100)
    word_voice = await get_word_voice(word_id)
    if not word_voice:
        download_failed_voice = f"{word_id}.mp3"
    rel_id = ""
    for d in word["104"]:
        if rel_id != d["relaId"]:  # 换词条了
            if rel_id:  # 如果有rel_id说明是第二个词条
                definition += "<br>"
            if d["lang"] == "ja":
                definition += f'<span class="jptext">・{d["title"]}</span>'
            else:
                definition += f"・{d['title']}"
        if d["lang"] == "ja" and d["relaId"] == rel_id:  # 同一词条的日语解释
            definition += f'<span class="jptext">({d["title"]})</span>'
        rel_id = d["relaId"]
    accent = word["result"][0]["accent"]
    tags = word["result"][0]["tags"].split("#") if "tags" in word["result"] else []
    tags.append("moji2anki")
    if source:
        tags.append(source)
    is_all_kana = is_kana(word["result"][0]["spell"])
    part_of_speech = "".join(regex_pattern.findall(word["result"][0]["excerpt"]))
    note_dict = {
        "model": card_model,
        "fields": [
            f"{word['result'][0]['spell']}{'[' + word['result'][0]['pron'] + ']' if not is_all_kana else ''}",
            accent,
            part_of_speech,
            "",
            "",
            definition,
            f"[sound:{word_id}.mp3]",
            f"{'' if is_all_kana else '1'}",
            "",
            "",
            "",
        ],
        "tags": tags,
    }
    note = genanki.Note(**note_dict)
    card_deck.add_note(note)

    return download_failed_voice


def extract_last_segment(url: str) -> str:
    parsed_url = urlparse(url)
    path_without_query = parsed_url.path
    path_segments = path_without_query.split("/")
    last_path_segment = next(
        (segment for segment in reversed(path_segments) if segment), ""
    )
    return last_path_segment


async def generate_anki_cards(
    word_list_url: str, task_id: str, update_progress: callable
):
    try:
        word_list_id = extract_last_segment(word_list_url)
        if word_list_id == "":
            raise DataError("词单id有误")
        word_list_name_list = []
        logger.info("正在获取单词列表第1页...")
        update_progress(task_id, "正在获取单词列表第1页...")
        word_list_1st_page = await get_data(
            WORD_LIST_API,
            {"fid": word_list_id, "count": 50, "sortType": 0, "pageIndex": 1},
        )
        word_list_all = word_list_1st_page["result"]["result"]
        word_list_name_list.append(word_list_1st_page["result"]["1000"][0]["title"])
        if len(word_list_1st_page["result"]["1000"]) > 1:
            word_list_name_list.append(word_list_1st_page["result"]["1000"][1]["title"])
            parent_word_list_id = word_list_1st_page["result"]["1000"][1]["objectId"]
            while True:
                word_list_detail = await get_data(
                    WORD_LIST_API,
                    {
                        "fid": parent_word_list_id,
                        "count": 50,
                        "sortType": 0,
                        "pageIndex": 1,
                    },
                )
                if len(word_list_detail["result"]["1000"]) == 1:
                    break
                word_list_name_list.append(
                    word_list_detail["result"]["1000"][1]["title"]
                )
                parent_word_list_id = word_list_detail["result"]["1000"][1]["objectId"]
            word_list_name_list.reverse()
            word_list_name_list = [x.replace(" ", "-") for x in word_list_name_list]
        word_list_name = "::".join(word_list_name_list)
        total_page = word_list_1st_page["result"]["totalPage"]
        for i in range(2, total_page + 1):
            await asyncio.sleep(random.randrange(15, 50) / 100)
            logger.info(f"正在获取单词列表第{i}页(共{total_page}页)...")
            update_progress(task_id, f"正在获取单词列表第{i}页(共{total_page}页)...")
            word_list = await get_data(
                WORD_LIST_API,
                {"fid": word_list_id, "count": 50, "sortType": 0, "pageIndex": i},
            )
            word_list_all.extend(word_list["result"]["result"])
        logger.info("单词列表获取完成，正在解析单词id...")
        update_progress(task_id, "单词列表获取完成，正在解析单词id...")
        word_ids = await get_word_id(word_list_all)
        word_ids = list(set(word_ids))
        if len(word_ids) == 0:
            raise DataError("词单中无单词")
        if len(word_ids) < word_list_1st_page["result"]["size"]:
            raise DataError("未登录，数据访问受限")
        logger.info("正在获取单词语音及生成单词卡片(耗时较久，请耐心等待)...")
        update_progress(
            task_id, "正在获取单词语音及生成单词卡片(耗时较久，请耐心等待)..."
        )
        for n, word in enumerate(word_ids, 1):
            logger.info(f"正在生成第{n}/{len(word_ids)}张单词卡片")
            update_progress(task_id, f"正在生成第{n}/{len(word_ids)}张单词卡片")
            download_failed_voice = await generate_word_card(word, word_list_name)
            if download_failed_voice:
                logger.warning(f"语音下载失败:{download_failed_voice}")
                update_progress(task_id, f"语音下载失败:{download_failed_voice}")
        card_package = genanki.Package(card_deck)
        card_package.media_files = [
            os.path.join(voice_temp_dir, x)
            for x in os.listdir(voice_temp_dir)
            if os.path.isfile(os.path.join(voice_temp_dir, x))
        ]
        card_package.media_files.extend(
            [
                os.path.join(font_dir, x)
                for x in os.listdir(font_dir)
                if os.path.isfile(os.path.join(font_dir, x))
            ]
        )
        card_package.write_to_file(os.path.join(deck_temp_dir, f"{task_id}.apkg"))
        update_progress(task_id, "SUCCESS")
    except Exception as e:
        logger.exception("Error occurred")
        update_progress(task_id, f"Failed: {str(type(e))[8:-2]}:{str(e)}")


async def purge_cache():
    entries = os.listdir(voice_temp_dir)
    for entry in entries:
        full_path = os.path.join(voice_temp_dir, entry)
        if os.path.isfile(full_path):
            os.remove(full_path)
            logger.debug(f"Deleted file: {full_path}")
    entries2 = os.listdir(deck_temp_dir)
    for entry in entries2:
        full_path = os.path.join(deck_temp_dir, entry)
        if os.path.isfile(full_path):
            os.remove(full_path)
            logger.debug(f"Deleted file: {full_path}")
