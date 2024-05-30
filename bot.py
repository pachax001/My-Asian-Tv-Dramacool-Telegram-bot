from datetime import datetime
import os
from time import time
import logging
import time as _time
from Utils.commons import load_yaml, pretty_time, threaded
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
from pyrogram import Client, filters
from Clients.DramaClient import DramaClient
import shutil
import asyncio
from dotenv import load_dotenv
import html
from pyrogram.enums import ParseMode
from Clients.BaseClient import BaseClient
import pyrogram.errors
import zipfile
from db import usersettings_collection as dbmongo
from bson.binary import Binary
import re
import math
from pymediainfo import MediaInfo
import sys
from io import BytesIO
import subprocess
last_update_time = 0
waiting_for_photo = False
waiting_for_caption = False
waiting_for_search_drama = False
waiting_for_new_caption = False
waiting_for_user_ep_range = False
waiting_for_mirror = False
telegram_upload = False
waiting_for_zip_mirror = False
ongoing_task = False
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
base_client = BaseClient()
handler = logging.FileHandler("log.txt")
handler.setLevel(logging.DEBUG)
stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.DEBUG)
formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)
stream_handler.setFormatter(formatter)
logger.addHandler(handler)
logger.addHandler(stream_handler)
config_file = "config_udb.yaml"
config = load_yaml(config_file)
downloader_config = config["DownloaderConfig"]
max_parallel_downloads = downloader_config["max_parallel_downloads"]
thumbpath = downloader_config["thumbpath"]
ep_range_msg = None
search_res_msg = None
select_res_msg = None
send_caption_msg = None
proceed_msg = None
user_ep_range = None
caption_view_msg = None


def is_file_in_directory(filename, directory):
    return 1 if os.path.isfile(os.path.join(directory, filename)) else 0


def is_thumb_in_db():
    doc = dbmongo.find_one()
    encoded_image = doc["thumbnail"]
    if encoded_image is not None:
        return 1
    else:
        return 0


def check_caption():
    if dbmongo is not None:
        document = dbmongo.find_one()
        if document is not None:
            caption = document["caption"]
            if caption is None:
                return 0
            else:
                return 1
        else:
            return 0


def convert_size(size_bytes):
    if size_bytes == 0:
        return "0B"
    size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return "%s %s" % (s, size_name[i])


if not os.path.exists(downloader_config["download_dir"]):
    logger.debug(f"Creating download directory:{downloader_config['download_dir']}...")
    os.makedirs(downloader_config["download_dir"])
if not os.path.exists(thumbpath):
    print(f"Creating thumbnail directory:{thumbpath}...")
    os.makedirs(thumbpath)
load_dotenv("config.env", override=True)
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    logger.error("No bot token provided.")
    sys.exit(1)
OWNER_ID = int(os.getenv("OWNER_ID"))
if not BOT_TOKEN:
    logger.error("No Owner id provided.")
    sys.exit(1)
API_ID = os.getenv("API_ID")
if not API_ID:
    logger.error("No API ID provided.")
    sys.exit(1)
API_HASH = os.getenv("API_HASH")
if not API_HASH:
    logger.error("No API hash provided.")
    sys.exit(1)
DEFAULT_RCLONE_PATH = os.getenv("DEFAULT_RCLONE_PATH")
if not DEFAULT_RCLONE_PATH:
    logger.info("No rclone path provided. Cloud upload won't work.")
RCLONE_CONF_PATH = os.getenv("RCLONE_CONF_PATH")
rclone_conf_file_path = os.path.join(RCLONE_CONF_PATH, "rclone.conf")
if not RCLONE_CONF_PATH or not os.path.isfile(rclone_conf_file_path):
    logger.info("No rclone.conf found. Cloud upload won't work.")
app = Client(
    "my_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    max_concurrent_transmissions=16,
)


def get_resolutions(items):
    """
    genarator function to yield the resolutions of available episodes
    """
    for item in items:
        yield [i for i in item.keys() if i not in ("error", "original")]


def downloader(ep_details, dl_config):
    """
    download function where Download Client initialization and download happens.
    Accepts two dicts: download config, episode details. Returns download status.
    """

    get_current_time = lambda fmt="%F %T": datetime.now().strftime(fmt)
    start = get_current_time()
    start_epoch = int(time())
    out_file = ep_details["episodeName"]
    if "downloadLink" not in ep_details:
        return f'[{start}] Download skipped for {out_file}, due to error: {ep_details.get("error", "Unknown")}'
    download_link = ep_details["downloadLink"]
    download_type = ep_details["downloadType"]
    referer = ep_details["refererLink"]
    out_dir = dl_config["download_dir"]

    if download_type == "hls":
        logger.debug(f"Creating HLS download client for {out_file}")
        from Utils.HLSDownloader import HLSDownloader
        dlClient = HLSDownloader(dl_config, referer, out_file)
    elif download_type == "mp4":
        logger.debug(f"Creating MP4 download client for {out_file}")
        from Utils.BaseDownloader import BaseDownloader
        dlClient = BaseDownloader(dl_config, referer, out_file)
    else:
        return (3,f"[{start}] Download skipped for {out_file}, due to unknown download type [{download_type}]",)
    logger.debug(f"Download started for {out_file}...")
    logger.info(f"Download started for {out_file}...")
    if os.path.isfile(os.path.join(f"{out_dir}", f"{out_file}")):

        return 0, f"[{start}] Download skipped for {out_file}. File already exists!"
    else:
        try:

            status, msg = dlClient.start_download(download_link)
        except Exception as e:
            status, msg = 1, str(e)

        dlClient._cleanup_out_dirs()
        end = get_current_time()
        if status != 0:
            return 1, f"[{end}] Download failed for {out_file}, with error: {msg}"
        end_epoch = int(time())
        download_time = pretty_time(end_epoch - start_epoch, fmt="h m s")
        return 2, f"[{end}] Download completed for {out_file} in {download_time}!"


def batch_downloader(download_fn, links, dl_config, max_parallel_downloads):
    @threaded(
        max_parallel=max_parallel_downloads,
        thread_name_prefix="udb-",
        print_status=False,
    )
    def call_downloader(link, dl_config):
        result = download_fn(link, dl_config)
        print("results from batch-downloader", result)
        return result
    dl_status = call_downloader(links.values(), dl_config)
    logger.debug(f"Download status: {dl_status}")
    # while not all(isinstance(result, tuple) and result[0] in {0, 1} for result in dl_status):
    #     total_progress = sum(dl[1].get_progress_percentage() for dl in dl_status if isinstance(dl, tuple) and dl[0] == 2)
    #     avg_progress = total_progress / len(dl_status)
    #     print(f"Overall Progress: {avg_progress:.2f}%")
    #     _time.sleep(10)


    # status_str = f"Download Summary:"
    # for status in dl_status:
    #     status_str += f"\n{status}"

    return dl_status


class DramaBot:
    def __init__(self, config):
        self.DCL = DramaClient(config["drama"])
        self.default_ep_range = "1-16"
        self.reset()
        self.semaphore = asyncio.Semaphore(16)

    def reset(self):
        logger.info("Resetting DramaBot...")
        self.waiting_for_ep_range = False
        self.ep_range = None
        self.ep_start = None
        self.ep_end = None
        self.specific_eps = []
        self.target_series = None
        self.episode_links = {}
        self.ep_infos = None
        self.target_dl_links = {}
        self.series_title = None
        self.episode_prefix = None
        self.search_results_message_id = None
        self.search_id = int(_time.time())
        self.search_results = {}
        self.DCL.udb_episode_dict.clear()
        base_client.udb_episode_dict.clear()
        if not os.path.exists(downloader_config["download_dir"]):
            logger.debug(f"Creating download directory:{downloader_config['download_dir']}...")
            os.makedirs(downloader_config["download_dir"])
        if not os.path.exists(thumbpath):
            print(f"Creating thumbnail directory:{thumbpath}...")
            os.makedirs(thumbpath)

    async def drama(self, client, message):
        self.reset()
        global ongoing_task
        global search_res_msg
        global waiting_for_search_drama
        keyword = " ".join(message.command[1:])
        if not keyword.strip():
            await message.reply_text("No search keyword provided.")
            ongoing_task = False
            return
        try:
            search_results = self.DCL.search(keyword)
            logger.info(f"Search results: {search_results}")
            self.search_results = {
                i + 1: result for i, result in enumerate(search_results.values())
            }
        except Exception as e:
            logger.error(f"An error occurred during search: {e}")
            await message.reply_text("An error occurred during the search.")
            return
        if not self.search_results:
            await message.reply_text("No results found.")
            ongoing_task = False
            return
        keyboard = [
            [
                InlineKeyboardButton(
                    f"{result['title']} ({result['year']})",
                    callback_data=f"{self.search_id}:{i+1}",
                )
            ]
            for i, result in enumerate(self.search_results.values())
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        search_res_msg = await message.reply_text(
            "Search Results:", reply_markup=reply_markup
        )
        waiting_for_search_drama = True
    async def shell(self,client, message):
        try:
            cmd = message.text.split(maxsplit=1)
            if len(cmd) == 1:
                await message.reply_text("No command to execute was given.")
                return

            process = await asyncio.create_subprocess_exec(
                *cmd[1].split(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            reply = ''
            if stdout:
                reply += f"*Stdout*\n{stdout.decode()}\n"
                logger.info(f"Shell - {cmd} - {stdout.decode()}")
            if stderr:
                reply += f"*Stderr*\n{stderr.decode()}"
                logger.error(f"Shell - {cmd} - {stderr.decode()}")

            if len(reply) > 3000:
                with BytesIO(str.encode(reply)) as out_file:
                    out_file.name = "shell_output.txt"
                    await app.send_document(message.chat.id, out_file)
            elif len(reply) != 0:
                await message.reply_text(reply)
            else:
                await message.reply_text('No Reply')
        except Exception as e:
            logger.error(f"Error in executing shell command: {e}")
            await message.reply_text(f"An error occurred: {e}")
    async def on_callback_query(self, client, callback_query):
        global ongoing_task
        global search_res_msg
        global waiting_for_search_drama
        search_id, series_index = map(int, callback_query.data.split(":"))
        if search_id != self.search_id:
            await client.answer_callback_query(
                callback_query.id,
                "Cannot perform search on previous results.",
                show_alert=True,
            )
            return
        await callback_query.message.edit_reply_markup(reply_markup=None)
        await app.delete_messages(callback_query.message.chat.id, search_res_msg.id)
        self.episode_links = {}
        self.ep_infos = None
        self.target_dl_links = {}
        self.series_title = None
        self.episode_prefix = None
        episodes = []
        logger.info(f"{series_index=}")
        self.target_series = self.search_results[series_index]
        title = self.target_series["title"]
        logger.info(f"{title=}")
        try:
            episodes = self.DCL.fetch_episodes_list(self.target_series)
        except Exception as e:
            logger.error(f"An error occurred during episode fetch: {e}")
            await callback_query.message.reply_text(
                "An error occurred during episode fetch."
            )
            ongoing_task = False
            return
        try:
            episodes_message = self.DCL.show_episode_results(
                episodes, (self.ep_start, self.ep_end)
            )
        except Exception as e:
            logger.error(f"An error occurred during episode fetch: {e}")
            await callback_query.message.reply_text(
                "An error occurred during episode fetch."
            )
            ongoing_task = False
            return
        global ep_message_ids
        ep_message_ids = []
        if episodes_message:
            messages = [
                episodes_message[i: i + 4096]
                for i in range(0, len(episodes_message), 4096)
            ]
            for message in messages:
                logger.info("Getting episodes")
                ep_msg = await callback_query.message.reply_text(message)
                ep_message_ids.append(ep_msg.id)
        else:
            await callback_query.message.reply_text("No episodes found.")
        await self.get_ep_range(client, callback_query.message, "Enter", None)
        waiting_for_search_drama = False

    async def on_message(self, client, message):
        global ep_message_ids
        global ep_range_msg
        global ongoing_task
        try:
            for ep_msg_id in ep_message_ids:
                await app.delete_messages(message.chat.id, ep_msg_id)
            await app.delete_messages(message.chat.id, ep_range_msg.id)
        except:
            pass
        if self.waiting_for_ep_range:
            self.waiting_for_ep_range = False
            self.ep_range = message.text or "all"
            if str(self.ep_range).lower() == "all":
                self.ep_range = self.default_ep_range
                self.mode = "all"
            else:
                self.mode = "custom"
            logger.info(f"Selected episode range ({self.mode=}): {self.ep_range=}")
            if self.ep_range.count("-") > 1:
                logger.error("Invalid input! You must specify only one range.")
                return
            self.ep_start, self.ep_end, self.specific_eps = 0, 0, []
            for ep_range in self.ep_range.split(","):
                if "-" in ep_range:
                    ep_range = ep_range.split("-")
                    if ep_range[0] == "":
                        ep_range[0] = self.default_ep_range.split("-")[0]
                    if ep_range[1] == "":
                        ep_range[1] = self.default_ep_range.split("-")[1]
                    self.ep_start, self.ep_end = map(float, ep_range)
                else:
                    self.specific_eps.append(float(ep_range))
            try:
                episodes = self.DCL.fetch_episodes_list(self.target_series)
            except Exception as e:
                logger.error(f"An error occurred during episode fetch: {e}")
                await message.reply_text("An error occurred during episode fetch.")
                ongoing_task = False
                return
            await self.show_episode_links(
                client, message, episodes, self.ep_start, self.ep_end, self.specific_eps
            )

    async def show_episode_links(
        self, client, message, episodes, ep_start, ep_end, specific_eps
    ):
        global select_res_msg
        global ep_infos_msg_id
        global ongoing_task
        message_patience = await message.reply_text(
            "Fetching episode links, Be patience😊😊😊..."
        )
        try:
            self.episode_links, self.ep_infos = self.DCL.fetch_episode_links(
                episodes, ep_start, ep_end, specific_eps
            )
        except Exception as e:
            logger.error(f"An error occurred during episode fetch: {e}")
            await message.reply_text("An error occurred during episode fetch.")
            ongoing_task = False
            return
        try:
            await app.delete_messages(message.chat.id, user_ep_range)
            await app.delete_messages(message.chat.id, message_patience.id)
        except:
            pass
        info_text = "\n".join(self.ep_infos)
        info_texts = [info_text[i: i + 4096]
                      for i in range(0, len(info_text), 4096)]
        ep_infos_msg_id = []
        for info in info_texts:
            ep_info_msg = await message.reply_text(info)
            ep_infos_msg_id.append(ep_info_msg.id)
        valid_resolutions = []
        valid_resolutions_gen = get_resolutions(self.episode_links.values())
        for _valid_res in valid_resolutions_gen:
            valid_resolutions = _valid_res
            if len(valid_resolutions) > 0:
                break
        else:
            valid_resolutions = ["360", "480", "720", "1080"]
        self.series_title, self.episode_prefix = self.DCL.set_out_names(
            self.target_series
        )
        downloader_config["download_dir"] = os.path.join( f"{downloader_config['download_dir']}", f"{self.series_title}")
        logger.info(f"{valid_resolutions=}")
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        text=res, callback_data=f"{self.search_id}:{res}"
                    )
                ]
                for res in valid_resolutions
            ]
        )
        select_res_msg = await message.reply_text(
            "Please select a resolution:", reply_markup=keyboard
        )

    async def get_ep_range(self, client, message, mode="Enter", _episodes_predef=None):
        global ep_range_msg
        global waiting_for_user_ep_range
        if _episodes_predef:
            self.ep_range = _episodes_predef
        else:
            ep_range_msg = await message.reply_text(
                f"\n{mode} episodes to download (ex: 1-16): "
            )
            self.waiting_for_ep_range = True
            waiting_for_user_ep_range = True

    async def on_callback_query_resoloution(self, client, callback_query):
        global ep_details_msg_ids
        global select_res_msg
        global ep_infos_msg_id
        ep_details_msg_ids = []
        global proceed_msg
        global ongoing_task
        await callback_query.message.edit_reply_markup(reply_markup=None)
        await app.delete_messages(callback_query.message.chat.id, select_res_msg.id)
        for ep_info_msg_id in ep_infos_msg_id:
            await app.delete_messages(callback_query.message.chat.id, ep_info_msg_id)
        search_id, resint = map(int, callback_query.data.split(":"))
        resolution = str(resint)
        if search_id != self.search_id:
            await client.answer_callback_query(
                callback_query.id,
                "Cannot select resolution on previous results.",
                show_alert=True,
            )
            return
        try:
            self.target_dl_links = self.DCL.fetch_m3u8_links(
                self.episode_links, resolution, self.episode_prefix
            )
        except Exception as e:
            logger.error(f"An error occurred during episode fetch: {e}")
            await callback_query.message.reply_text(
                "An error occurred during episode fetch."
            )
            ongoing_task = False
            return
        all_ep_details_text = ""
        for ep, details in self.target_dl_links.items():
            episode_name = details["episodeName"]
            episode_subs = details["episodeSubs"]
            ep_details_text = (
                f"Episode {ep}:\nName: {episode_name}\nSubs: {episode_subs}"
            )
            all_ep_details_text += ep_details_text
        all_ep_details_texts = [
            all_ep_details_text[i: i + 4096]
            for i in range(0, len(all_ep_details_text), 4096)
        ]
        for text in all_ep_details_texts:
            ep_details_msg = await callback_query.message.reply_text(text)
            ep_details_msg_ids.append(ep_details_msg.id)
        available_dl_count = len(
            [
                k
                for k, v in self.target_dl_links.items()
                if v.get("downloadLink") is not None
            ]
        )
        logger.info("Links Found!!")
        msg = f"Episodes available for download [{available_dl_count}/{len(self.target_dl_links)}].Proceed to download?"
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Yes", callback_data=f"{self.search_id}:download_yes"
                    ),
                    InlineKeyboardButton(
                        "No", callback_data=f"{self.search_id}:download_no"
                    ),
                ]
            ]
        )
        proceed_msg = await callback_query.message.reply_text(
            msg, reply_markup=keyboard
        )
        if len(self.target_dl_links) == 0:
            logger.error("No episodes available to download! Exiting.")
            await callback_query.message.reply_text(
                "No episodes available to download! Exiting."
            )
            await callback_query.message.edit_reply_markup(reply_markup=None)
            ongoing_task = False
            return

    async def on_callbackquery_download(self, client, callback_query):
        global telegram_upload
        global waiting_for_mirror
        global waiting_for_zip_mirror
        global ongoing_task
        if telegram_upload:
            async def send_document(
                client,
                chat_id,
                document,
                progress,
                progress_args,
                thumb=None,
                caption=None,
                parse_mode=None,
            ):
                async with self.semaphore:
                    filename = os.path.basename(document)
                    logger.debug(f"Uploading {filename} with {os.getpid()}")
                    await asyncio.sleep(1)
                    message = await client.send_message(
                        chat_id, f"Starting upload of {filename}"
                    )
                    try:
                        return await client.send_document(
                            chat_id,
                            document=document,
                            progress=progress,
                            progress_args=(
                                message,
                                *progress_args,
                            ),
                            thumb=thumb,
                            caption=caption,
                            parse_mode=parse_mode,
                        )
                    finally:
                        await client.delete_messages(chat_id, message.id)
            search_id, action = callback_query.data.split(":")
            int_search_id = int(search_id)
            if int_search_id != self.search_id:
                await client.answer_callback_query(
                    callback_query.id,
                    "Cannot download previous selections",
                    show_alert=True,
                )
                return
            if action == "download_yes":
                await callback_query.message.edit_reply_markup(reply_markup=None)
                await app.delete_messages(
                    callback_query.message.chat.id, proceed_msg.id
                )
                for ep_details_msg_id in ep_details_msg_ids:
                    await app.delete_messages(
                        callback_query.message.chat.id, ep_details_msg_id
                    )
                start_msg = await callback_query.message.reply_text(
                    "Downloading episodes..."
                )
                logger.info("Downloading episodes...")
                download_results = batch_downloader(
                    downloader,
                    self.target_dl_links,
                    downloader_config,
                    max_parallel_downloads,
                )
                status_message_ids = []
                for status, message in download_results:
                    sent_message = await callback_query.message.reply_text(message)
                    status_message_ids.append(sent_message.id)
                await client.delete_messages(
                    callback_query.message.chat.id, start_msg.id
                )
                directory = downloader_config["download_dir"]
                last_update_times = {}

                async def progress(current, total, message, filename):
                    now = _time.time()
                    pct = current * 100 / total
                    pct_str = f"{filename} : {pct:.1f}%"
                    convert_total = convert_size(total)
                    convert_current = convert_size(current)
                    pct = float(str(pct).strip("%"))
                    p = min(max(pct, 0), 100)
                    cFull = int(p // 8)
                    cPart = int(p % 8 - 1)
                    p_str = "■" * cFull
                    if cPart >= 0:
                        p_str += ["▤", "▥", "▦", "▧", "▨", "▩", "■"][cPart]
                    p_str += "□" * (12 - cFull)
                    progress_bar = f"[{p_str}]"
                    progress_text = f"Uploading to telegram {pct_str} {progress_bar} {convert_current}/{convert_total}" 
                    if message.text != progress_text:
                        if (
                            filename not in last_update_times
                            or now - last_update_times[filename] > 10
                        ):
                            try:
                                await message.edit_text(progress_text)
                                last_update_times[filename] = now
                            except (
                                pyrogram.errors.exceptions.bad_request_400.MessageNotModified
                            ):
                                pass
                    await asyncio.sleep(1)
                try:
                    upload_tasks = []
                    for filename in os.listdir(directory):
                        filepath = os.path.join(directory, filename)
                        if os.path.isfile(filepath) and filename.endswith(".mp4"):
                            media_info = MediaInfo.parse(filepath)
                            for track in media_info.tracks:
                                if track.track_type == "Video":
                                    milliseconds = track.duration
                                    if milliseconds is not None:
                                        seconds, milliseconds = divmod(
                                            milliseconds, 1000
                                        )
                                        minutes, seconds = divmod(seconds, 60)
                                        hours, minutes = divmod(minutes, 60)
                                        if hours > 0:
                                            duration = f"{hours}h{minutes}m{seconds}s"
                                        elif minutes > 0:
                                            duration = f"{minutes}m{seconds}s"
                                        else:

                                            duration = f"{seconds}s"
                                    else:
                                        duration = "Unknown"
                                    file_size = os.path.getsize(filepath)
                                    print(f"File size: {file_size}")
                                    file_size_con = convert_size(file_size)
                                    break
                            doc = dbmongo.find_one()
                            encoded_image = doc["thumbnail"]
                            known_keys = {"filename", "size", "duration"}
                            def format_caption(caption, **kwargs):
                                try:
                                    return caption.format(**kwargs)
                                except KeyError as e:
                                    print(f"Caption contains unrecognized format: {e}")
                                    unrecognized_key = str(e).strip("'")
                                    # Remove the unrecognized format placeholder
                                    return re.sub(rf"\{{{unrecognized_key}\}}", "", caption)
                            unformat_caption_db = doc["caption"]
                            caption_db = format_caption(unformat_caption_db, filename=filename, size=file_size_con, duration=duration)
                            if encoded_image is not None and caption_db is None:
                                with open("thumbnail.jpg", "wb") as f:
                                    f.write(encoded_image)
                                    task = send_document(
                                        client,
                                        callback_query.from_user.id,
                                        document=filepath,
                                        progress=progress,
                                        progress_args=(
                                            os.path.basename(filename),),
                                        thumb="thumbnail.jpg",
                                    )
                                    upload_tasks.append(
                                        asyncio.create_task(task))
                            elif encoded_image is None and caption_db is None:
                                task = send_document(
                                    client,
                                    callback_query.from_user.id,
                                    document=filepath,
                                    progress=progress,
                                    progress_args=(
                                        os.path.basename(filename),),
                                )
                                upload_tasks.append(asyncio.create_task(task))
                            elif encoded_image is not None and caption_db is not None:
                                file_name = html.escape(
                                    os.path.basename(filepath))
                                caption = caption_db.format(
                                    filename=html.escape(file_name),
                                    size=html.escape(file_size_con),
                                    duration=html.escape(duration),
                                )
                                with open("thumbnail.jpg", "wb") as f:
                                    f.write(encoded_image)
                                    task = send_document(
                                        client,
                                        callback_query.from_user.id,
                                        document=filepath,
                                        progress=progress,
                                        progress_args=(
                                            os.path.basename(filename),),
                                        thumb="thumbnail.jpg",
                                        caption=caption,
                                        parse_mode=ParseMode.HTML,
                                    )
                                    upload_tasks.append(
                                        asyncio.create_task(task))
                            elif encoded_image is None and caption_db is not None:
                                file_name = html.escape(
                                    os.path.basename(filepath))
                                caption = caption_db.format(
                                    filename=html.escape(file_name),
                                    size=html.escape(file_size_con),
                                    duration=html.escape(duration),
                                )
                                task = send_document(
                                    client,
                                    callback_query.from_user.id,
                                    document=filepath,
                                    progress=progress,
                                    progress_args=(
                                        os.path.basename(filename),),
                                    caption=caption,
                                    parse_mode=ParseMode.HTML,
                                )
                                upload_tasks.append(asyncio.create_task(task))
                    try:
                        await asyncio.gather(*upload_tasks)
                    except Exception as e:
                        logger.error(
                            f"An error occurred while sending files: {e}")
                        await app.send_message(
                            callback_query.message.chat.id,
                            f"An error occurred while sending files.",
                        )
                    finally:
                        self.reset()
                        telegram_upload = False
                        ongoing_task = False
                except Exception as e:
                    logger.error(f"An error occurred while sending files: {e}")
                    await app.send_message(
                        callback_query.message.chat.id,
                        "An error occurred while sending files.",
                    )
                    ongoing_task = False
                    telegram_upload = False
                finally:
                    if os.path.exists(directory):
                        try:
                            shutil.rmtree(directory)
                            logger.info(f"Deleted {directory}")
                            ongoing_task = False
                            telegram_upload = False
                            await app.send_message(
                                callback_query.message.chat.id,
                                "All Episodes Uploaded.",
                            )
                            for status_msg_id in status_message_ids:
                                await app.delete_messages(
                                    callback_query.message.chat.id, status_msg_id
                                )
                                await asyncio.sleep(1)
                        except Exception as e:
                            logger.error(f"An error occurred while deleting {directory}: {e}")
                            await app.send_message(
                                callback_query.message.chat.id,f"An error occurred while deleting {directory}.",)
                            ongoing_task = False
                            telegram_upload = False
            else:
                await callback_query.message.reply_text("Download cancelled.")
                await callback_query.message.edit_reply_markup(reply_markup=None)
                await app.delete_messages(
                    callback_query.message.chat.id, proceed_msg.id
                )
                for ep_details_msg_id in ep_details_msg_ids:
                    await app.delete_messages(
                        callback_query.message.chat.id, ep_details_msg_id
                    )
                self.reset()
                logger.info("Download cancelled.")
                ongoing_task = False
                telegram_upload = False
                try:
                    if os.path.exists(directory):
                        shutil.rmtree(directory)
                except Exception as e:
                    logger.error(f"An error occurred while deleting {directory}: {e}")
        elif waiting_for_mirror:
            search_id, action = callback_query.data.split(":")
            int_search_id = int(search_id)
            if int_search_id != self.search_id:
                await client.answer_callback_query(
                    callback_query.id,
                    "Cannot download previous selections",
                    show_alert=True,
                )
                return
            if action == "download_yes":
                await callback_query.message.edit_reply_markup(reply_markup=None)
                await app.delete_messages(
                    callback_query.message.chat.id, proceed_msg.id
                )
                for ep_details_msg_id in ep_details_msg_ids:
                    await app.delete_messages(
                        callback_query.message.chat.id, ep_details_msg_id
                    )
                start_msg = await callback_query.message.reply_text(
                    "Downloading episodes..."
                )
                logger.debug("Downloading episodes...")
                download_results = batch_downloader(
                    downloader,
                    self.target_dl_links,
                    downloader_config,
                    max_parallel_downloads,
                )
                status_message_ids = []
                for status, message in download_results:
                    sent_message = await callback_query.message.reply_text(message)
                    status_message_ids.append(sent_message.id)
                await client.delete_messages(
                    callback_query.message.chat.id, start_msg.id
                )
                directory = downloader_config["download_dir"]

                def get_progress_bar_string(pct):
                    pct = float(str(pct).strip("%"))
                    p = min(max(pct, 0), 100)
                    cFull = int(p // 8)
                    cPart = int(p % 8 - 1)
                    p_str = "■" * cFull
                    if cPart >= 0:
                        p_str += ["▤", "▥", "▦", "▧", "▨", "▩", "■"][cPart]
                    p_str += "□" * (12 - cFull)
                    return f"[{p_str}]"

                async def rclone_copy(filepath, upload_msg, filename):
                    cmd = [
                        "rclone",
                        "copy",
                        f"--config={RCLONE_CONF_PATH}rclone.conf",
                        filepath,
                        f"{DEFAULT_RCLONE_PATH}",
                        "-P",
                    ]
                    logger.debug(cmd)
                    process = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    progress_regex = re.compile(
                        r"Transferred:\s+([\d.]+\s*\w+)\s+/\s+([\d.]+\s*\w+),\s+([\d.]+%)\s*,\s+([\d.]+\s*\w+/s),\s+ETA\s+([\dwdhms]+)"
                    )
                    last_progress = None
                    update_interval = 10
                    last_update_time = _time.time()
                    while True:
                        line = await process.stdout.readline()
                        if not line:
                            break
                        line = line.decode().strip()
                        print("lines", line)
                        match = progress_regex.findall(line)
                        if match:
                            transferred, total, percent, speed, eta = match[0]
                            progress_bar = get_progress_bar_string(percent)
                            current_progress = f"Transferred: {transferred}, Total: {total}, Percent: {percent}, Speed: {speed}, ETA: {eta}"
                            if current_progress != last_progress:
                                if _time.time() - last_update_time >= update_interval:
                                    try:
                                        await app.edit_message_text(
                                            callback_query.message.chat.id,
                                            upload_msg.id,
                                            f"Upload progress of {filename}: {progress_bar}Transferred {transferred} out of Total {total} {percent} ETA: {eta} Speed: {speed}",)
                                        last_update_time = _time.time()
                                    except pyrogram.errors.MessageNotModified:
                                        pass
                                last_progress = current_progress
                    await process.wait()
                    if process.returncode != 0:
                        logger.debug(f"Error copying file {filepath}")
                    else:
                        logger.debug(f"Successfully copied file {filepath}")
                    return process.returncode

                async def upload_files(directory):
                    global waiting_for_mirror
                    global ongoing_task
                    try:
                        files = [
                            os.path.join(directory, filename)
                            for filename in os.listdir(directory)
                            if os.path.isfile(os.path.join(directory, filename))
                            and filename.endswith(".mp4")
                        ]
                        semaphore = asyncio.Semaphore(16)

                        async def upload_file(filepath):
                            async with semaphore:
                                filename = os.path.basename(filepath)
                                upload_msg = await app.send_message(
                                    callback_query.message.chat.id,
                                    f"Starting upload of {os.path.basename(filepath)}...",)
                                return_code = await rclone_copy(
                                    filepath, upload_msg, filename
                                )
                                if return_code == 0:
                                    await app.edit_message_text(
                                        callback_query.message.chat.id,
                                        upload_msg.id,
                                        f"Upload of {filename} is completed.",
                                    )
                                else:
                                    await app.edit_message_text(
                                        callback_query.message.chat.id,
                                        upload_msg.id,
                                        f"Error uploading {filename}.",
                                    )

                        tasks = [upload_file(filepath) for filepath in files]
                        await asyncio.gather(*tasks)
                        await app.send_message(
                            callback_query.message.chat.id,
                            "All uploads completed",
                        )
                        for status_msg_id in status_message_ids:
                            await app.delete_messages(
                                callback_query.message.chat.id, status_msg_id
                            )
                            await asyncio.sleep(1)
                        shutil.rmtree(directory)
                        waiting_for_mirror = False
                        ongoing_task = False
                    except Exception as e:
                        logger.error(f"An error occurred while uploading files: {e}")
                        await app.send_message(
                            callback_query.message.chat.id,
                            "An error occurred while uploading files.",
                        )
                        waiting_for_mirror = False
                        ongoing_task = False
                await upload_files(directory)
            else:
                await callback_query.message.reply_text("Download cancelled.")
                await callback_query.message.edit_reply_markup(reply_markup=None)
                await app.delete_messages(
                    callback_query.message.chat.id, proceed_msg.id
                )
                for ep_details_msg_id in ep_details_msg_ids:
                    await app.delete_messages(
                        callback_query.message.chat.id, ep_details_msg_id
                    )
                self.reset()
                waiting_for_mirror = False
                ongoing_task = False
                logger.debug("Download cancelled.")
                try:
                    if os.path.exists(directory):
                        shutil.rmtree(directory)
                except Exception as e:
                    logger.error(f"An error occurred while deleting {directory}: {e}")
        elif waiting_for_zip_mirror:
            logger.info("Waiting for zip mirror")
            search_id, action = callback_query.data.split(":")
            int_search_id = int(search_id)
            if int_search_id != self.search_id:
                await client.answer_callback_query(
                    callback_query.id,
                    "Cannot download previous selections",
                    show_alert=True,
                )
                return
            if action == "download_yes":
                await callback_query.message.edit_reply_markup(reply_markup=None)
                await app.delete_messages(
                    callback_query.message.chat.id, proceed_msg.id
                )
                for ep_details_msg_id in ep_details_msg_ids:
                    await app.delete_messages(
                        callback_query.message.chat.id, ep_details_msg_id
                    )
                start_msg = await callback_query.message.reply_text(
                    "Downloading episodes..."
                )
                logger.debug("Downloading episodes...")
                download_results = batch_downloader(
                    downloader,
                    self.target_dl_links,
                    downloader_config,
                    max_parallel_downloads,
                )
                status_message_ids = []
                for status, message in download_results:
                    sent_message = await callback_query.message.reply_text(message)
                    status_message_ids.append(sent_message.id)
                await client.delete_messages(
                    callback_query.message.chat.id, start_msg.id
                )
                directory = downloader_config["download_dir"]
                def format_size(size):
    
                    units = ['B', 'KB', 'MB', 'GB', 'TB']
                    unit = 0
                    while size >= 1024:
                        size /= 1024
                        unit += 1
                    return f"{size:.2f} {units[unit]}"

                async def send_progress(chat_id, message_id, filename, total, current):
                    percent = round((current / total) * 100, 2)
                    progress_bar = get_progress_bar_string(percent)
                    total_str = format_size(total)
                    current_str = format_size(current)
                    await app.edit_message_text(
                        chat_id,
                        message_id,
                        f"Zipping progress of {filename}: {progress_bar}{percent}% ({current_str} / {total_str})",)

                async def create_zip_with_progress(src, dst, chat_id, message_id):
                    zf = zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED)
                    total_size = sum(
                        os.path.getsize(os.path.join(dirpath, filename))
                        for dirpath, dirnames, filenames in os.walk(src)
                        for filename in filenames
                    )
                    current_size = 0
                    last_update_time = _time.time()
                    for dirpath, dirnames, filenames in os.walk(src):
                        for filename in filenames:
                            file_path = os.path.join(dirpath, filename)
                            zf.write(file_path, os.path.relpath(
                                file_path, src))
                            current_size += os.path.getsize(file_path)
                            if _time.time() - last_update_time >= 10:
                                await send_progress(
                                    chat_id,
                                    message_id,
                                    os.path.basename(dst),
                                    total_size,
                                    current_size,
                                )
                                last_update_time = _time.time()
                    zf.close()

                def get_progress_bar_string(pct):
                    pct = float(str(pct).strip("%"))
                    p = min(max(pct, 0), 100)
                    cFull = int(p // 8)
                    cPart = int(p % 8 - 1)
                    p_str = "■" * cFull
                    if cPart >= 0:
                        p_str += ["▤", "▥", "▦", "▧", "▨", "▩", "■"][cPart]
                    p_str += "□" * (12 - cFull)
                    return f"[{p_str}]"

                async def rclone_copy(filepath, upload_msg, filename):
                    cmd = [
                        "rclone",
                        "copy",
                        f"--config={RCLONE_CONF_PATH}rclone.conf",
                        filepath,
                        f"{DEFAULT_RCLONE_PATH}",
                        "-P",
                    ]
                    process = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    progress_regex = re.compile(
                        r"Transferred:\s+([\d.]+\s*\w+)\s+/\s+([\d.]+\s*\w+),\s+([\d.]+%)\s*,\s+([\d.]+\s*\w+/s),\s+ETA\s+([\dwdhms]+)"
                    )
                    last_progress = None
                    update_interval = 10
                    last_update_time = _time.time()
                    while True:
                        line = await process.stdout.readline()
                        if not line:
                            break
                        line = line.decode().strip()
                        print("lines", line)
                        match = progress_regex.findall(line)
                        if match:
                            transferred, total, percent, speed, eta = match[0]
                            progress_bar = get_progress_bar_string(percent)
                            current_progress = f"Transferred: {transferred}, Total: {total}, Percent: {percent}, Speed: {speed}, ETA: {eta}"
                            if current_progress != last_progress:
                                if _time.time() - last_update_time >= update_interval:
                                    try:
                                        await app.edit_message_text(
                                            callback_query.message.chat.id,
                                            upload_msg.id,
                                            f"Upload progress of {filename}: {progress_bar} Transferred {transferred} out of Total {total} {percent} ETA: {eta} Speed: {speed}",)
                                        last_update_time = _time.time()
                                    except pyrogram.errors.MessageNotModified:
                                        pass
                                last_progress = current_progress
                    await process.wait()
                    if process.returncode != 0:
                        logger.error(f"Error copying file {filepath}")
                    else:
                        logger.info(f"Successfully copied file {filepath}")
                    return process.returncode

                async def upload_files(directory):
                    global waiting_for_zip_mirror
                    global ongoing_task
                    try:
                        zip_file = f"{directory}.zip"
                        zip_msg = await app.send_message(
                            callback_query.message.chat.id, f"Starting zipping..."
                        )
                        await create_zip_with_progress(
                            directory,
                            zip_file,
                            callback_query.message.chat.id,
                            zip_msg.id,
                        )
                        await app.edit_message_text(
                            callback_query.message.chat.id,
                            zip_msg.id,
                            f"Zipping completed. Zip file: {os.path.basename(zip_file)}",)
                        await app.delete_messages(
                            callback_query.message.chat.id, zip_msg.id
                        )
                        upload_msg = await app.send_message(
                            callback_query.message.chat.id, "Starting upload..."
                        )
                        return_code = await rclone_copy(
                            zip_file, upload_msg, os.path.basename(zip_file)
                        )
                        if return_code == 0:
                            await app.edit_message_text(
                                callback_query.message.chat.id,
                                upload_msg.id,
                                f"Upload of {os.path.basename(zip_file)} is completed.",)
                        else:
                            await app.edit_message_text(
                                callback_query.message.chat.id,
                                upload_msg.id,
                                f"Error uploading {os.path.basename(zip_file)}.",
                            )
                        try:
                            for status_msg_id in status_message_ids:
                                await app.delete_messages(
                                    callback_query.message.chat.id, status_msg_id
                                )
                                await asyncio.sleep(1)
                            shutil.rmtree(directory)
                            os.remove(zip_file)
                        except Exception as e:
                            logger.error(
                                f"An error occurred while deleting {directory}: {e}"
                            )
                        waiting_for_zip_mirror = False
                        ongoing_task = False
                    except Exception as e:
                        logger.error(f"An error occurred: {e}")
                await upload_files(directory)
            else:
                await callback_query.message.reply_text("Download cancelled.")
                await callback_query.message.edit_reply_markup(reply_markup=None)
                await app.delete_messages(
                    callback_query.message.chat.id, proceed_msg.id
                )
                for ep_details_msg_id in ep_details_msg_ids:
                    await app.delete_messages(
                        callback_query.message.chat.id, ep_details_msg_id
                    )
                self.reset()
                logger.info("Download cancelled.")
                ongoing_task = False
                waiting_for_zip_mirror = False
                try:
                    if os.path.exists(directory):
                        shutil.rmtree(directory)
                except Exception as e:
                    logger.error(f"An error occurred while deleting {directory}: {e}")


bot = DramaBot(config)
msgpot = None
msgopt_id = None


@app.on_message(filters.command("start", prefixes="/"))
async def start(client, message):
    await app.set_bot_commands(
        [
            BotCommand("start", "Start the bot"),
            BotCommand("drama", "Search and download dramas"),
            BotCommand(
                "mirrordrama",
                "Can use /md also.Search and download dramas and upload to cloud",
            ),
            BotCommand(
                "zipmirrordrama",
                "Can use /zmd also.Search and download dramas and upload to cloud as zip",
            ),
            BotCommand(
                "usetting",
                "Can use /us also.Set thumbnail and caption for uploaded media",
            ),
            BotCommand("help", "Get help"),
            BotCommand("log", "Get log.txt"),
            BotCommand("shell", "Execute Shell commands"),
        ]
    )
    if message.from_user.id != OWNER_ID:
        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(
                    text="Owner", url="https://t.me/gunaya001")],
                [
                    InlineKeyboardButton(
                        text="Join Kdrama Request Group",
                        url="https://t.me/kdramasmirrorchat",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Join Ongoing Kdrama Channel",
                        url="https://t.me/kdramasmirrorlog",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Bot Repo",
                        url="https://github.com/pachax001/My-Asian-Tv-Dramacool-Telegram-bot",
                    )
                ],
            ]
        )
        await message.reply_text(
            "You are not authorized to use this bot.", reply_markup=keyboard
        )
        return
    elif message.from_user.id == OWNER_ID:
        await message.reply_text(
            "Download Dramas From https://myasiantv.ac/\n\nUse /help to get help.",
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(
                    text="Owner", url="https://t.me/gunaya001")]]
            ),
        )


@app.on_message(filters.command("drama") & filters.user(OWNER_ID))
async def drama(client, message):
    global ongoing_task
    if ongoing_task:
        await message.reply_text("Another task is ongoing. Please wait.")
        return
    global telegram_upload
    ongoing_task = True
    telegram_upload = True
    await bot.drama(client, message)
@app.on_message(filters.command("shell") & filters.user(OWNER_ID))
async def shell(client, message):
    await bot.shell(client, message)

@app.on_message(
    (filters.command("mirrordrama") | filters.command("md")) & filters.user(OWNER_ID)
)
async def mirrordrama(client, message):
    if not DEFAULT_RCLONE_PATH or not RCLONE_CONF_PATH:
        await message.reply_text("Cloud upload not configured.")
        logger.info("No rclone path provided. Cloud upload won't work.")
        return
    global ongoing_task
    if ongoing_task:
        await message.reply_text("Another task is ongoing. Please wait.")
        return
    global waiting_for_mirror
    waiting_for_mirror = True
    ongoing_task = True
    await bot.drama(client, message)


@app.on_message(
    (filters.command("zipmirrordrama") | filters.command("zmd"))
    & filters.user(OWNER_ID)
)
async def zipmirrordrama(client, message):
    if not DEFAULT_RCLONE_PATH or not RCLONE_CONF_PATH:
        await message.reply_text("Cloud upload not configured.")
        logger.info("No rclone path provided. Cloud upload won't work.")
        return
    global ongoing_task
    if ongoing_task:
        await message.reply_text("Another task is ongoing. Please wait.")
        return
    global waiting_for_zip_mirror
    waiting_for_zip_mirror = True
    ongoing_task = True
    await bot.drama(client, message)


@app.on_message(filters.command("help") & filters.user(OWNER_ID))
async def help(client, message):
    await message.reply_text(
        "Download Dramas From https://myasiantv.ac/\n\nUse /drama {drama name} to search for a drama.\n\nUse /md {drama name} or /mirrordrama {drama name} to upload to selected rclone drive.\n\nUse /zmd {drama name} or /zipmirordrama {drama name} to zip the downloaded dramas and upload them.\n\nUse /usetting to set thumbnail and caption for uploaded media.\nCaption has filterings for {filename}, {size}, {duration}.\n\nUse {filename} to display the filename, {size} to display the file size and {duration} to display the duration of the video in caption.\n\nCaption also supports HTML formatting.\n\nHTML formattings can be found here https://core.telegram.org/bots/api",
        disable_web_page_preview=True,
    )

@app.on_message(filters.command("log", prefixes="/"))
async def send_log(client, message):
    if message.from_user.id != OWNER_ID:
        return
    try:
        user_id = message.from_user.id
        if user_id == OWNER_ID:

            chat_id = message.chat.id
            log_file_path = os.path.join(os.path.dirname(__file__), "log.txt")
            await app.send_document(chat_id, document=log_file_path)
        else:
            await message.reply_text("Only Owner can use this command")
    except Exception as e:
        logging.error(f"Error processing log file: {e}")
        await message.reply_text("An error occured while processsing the log file.")
@app.on_message(
    (filters.command("usetting") | filters.command("us")) & filters.user(OWNER_ID)
)
async def usetting(client, message):
    if dbmongo is None:
        await app.send_message(
            message.chat.id,
            "No database connection. Add DATABASE_URL to config.env to use this feature.",
        )
        return
    if " ".join(message.command[1:]) == "-s thumb":
        if message.reply_to_message and message.reply_to_message.photo:
            path = os.path.join(thumbpath, "thumbnail.jpg")
            if os.path.exists(path):
                os.remove(path)
            await client.download_media(
                message.reply_to_message.photo.file_id, file_name=path
            )
            if os.path.exists(path):
                with open(path, "rb") as f:
                    encoded_image = Binary(f.read())
                    filter = {}
                    update = {
                        "$set": {"thumbnail": encoded_image}
                    }
                    dbmongo.update_one(filter, update)
                os.remove(path)
            await message.reply_text("Thumbnail added.")
            await client.delete_messages(
                chat_id=message.chat.id, message_ids=message.reply_to_message.id
            )
            return
        else:
            await message.reply_text("Please reply to a photo to add it as thumbnail.")
            return
    elif " ".join(message.command[1:]) == "-s caption":
        if message.reply_to_message and message.reply_to_message.text:
            caption = message.reply_to_message.text
            print(caption)
            filter = {}
            update = {
                "$set": {"caption": caption}
            }
            dbmongo.update_one(filter, update)
            await message.reply_text("Capton added.")
            await client.delete_messages(
                chat_id=message.chat.id, message_ids=message.reply_to_message.id
            )
            return
        else:
            await message.reply_text("Please reply to a message to add it as caption.")
            return
    elif " ".join(message.command[1:]).startswith("-s"):
        if len(message.command) > 2 and message.command[2] not in ["thumb", "caption"]:
            await message.reply_text(
                "Invalid command. Here are the valid commands:\n\nHere cmd is for usetting or us command\n\nsReply to the Value with appropriate arg respectively to set directly without opening usetting."
                "Custom Thumbnail:\n"
                "/cmd -s thumb\n\n"
                "Leech Filename Caption:\n"
                "/cmd -s caption"
            )
            return
    result = is_thumb_in_db()
    keyboard_buttons_thumb = [
        [InlineKeyboardButton(text="View thumbnail", callback_data="th:view")],
        [InlineKeyboardButton(text="Delete thumbnail",
                              callback_data="th:delete")],
        [InlineKeyboardButton(text="Change Thumbnail",
                              callback_data="th:add")],
    ]
    keyboard_buttons_without_thumb = [
        [InlineKeyboardButton(text="Add thumbnail", callback_data="th:add")]
    ]
    if check_caption() == 0 and result == 0:
        keyboard_buttons_without_thumb.append(
            [InlineKeyboardButton(text="Caption", callback_data="th:caption")]
        )
    elif check_caption() == 1 and result == 0:
        keyboard_buttons_without_thumb.append(
            [InlineKeyboardButton(text="✅Caption", callback_data="th:caption")]
        )
    no_thumb_keyboard = InlineKeyboardMarkup(keyboard_buttons_without_thumb)
    if result == 1 and check_caption() == 1:
        keyboard_buttons_thumb.append(
            [InlineKeyboardButton(text="✅Caption", callback_data="th:caption")]
        )
    elif result == 1 and check_caption() == 0:
        keyboard_buttons_thumb.append(
            [InlineKeyboardButton(text="Caption", callback_data="th:caption")]
        )
    with_thumb_keyboard = InlineKeyboardMarkup(keyboard_buttons_thumb)
    global msgopt
    global msgopt_id
    doc = dbmongo.find_one()
    db_caption = doc["caption"]
    if db_caption is not None:
        caption = db_caption
    else:
        caption = None
    if result == 0:
        msgopt = await message.reply_text(
            f"**Choose option**\n**Caption:** `{caption}`",
            reply_markup=no_thumb_keyboard,
        )
        msgopt_id = msgopt.id
    else:
        msgopt = await message.reply_text(
            f"**Choose option**\n**Caption is:** `{caption}`",
            reply_markup=with_thumb_keyboard,
            parse_mode=ParseMode.MARKDOWN,
        )
        msgopt_id = msgopt.id
async def is_ffmpeg_running():
    # Check if ffmpeg process is running
    try:
        subprocess.run(["pgrep", "ffmpeg"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except subprocess.CalledProcessError:
        return False
async def is_rclone_running():
    # Check if ffmpeg process is running
    try:
        subprocess.run(["pgrep", "rclone"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except subprocess.CalledProcessError:
        return False
@app.on_message(filters.command("cancel") & filters.user(OWNER_ID))
async def cancel(self,client,message):
    
    try:
        if await is_ffmpeg_running():
            proc = await asyncio.create_subprocess_exec("pkill", "-9", "-f", "ffmpeg")
            await proc.communicate()                
    except Exception as e:
        logger.error("Error while killing ffmpeg: %s", e)
        pass
    try:
        if await is_rclone_running():
            proc = await asyncio.create_subprocess_exec("pkill", "-9", "-f", "rclone")
            await proc.communicate()                
    except Exception as e:
        logger.error("Error while killing rclone: %s", e)
    if os.path.exists("downloads"):
        try:
            shutil.rmtree("downloads")
            logger.info("Deleted downloads folder")
        except Exception as e:
            logger.error("Failed to delete downloads folder: %s", e)
            pass
    global waiting_for_mirror
    global waiting_for_zip_mirror
    global waiting_for_caption
    global waiting_for_new_caption
    global waiting_for_photo
    global waiting_for_user_ep_range
    global ongoing_task
    global telegram_upload
    if waiting_for_mirror or waiting_for_zip_mirror or waiting_for_caption or waiting_for_new_caption or waiting_for_photo or waiting_for_user_ep_range or ongoing_task or telegram_upload:
        waiting_for_mirror = False
        waiting_for_zip_mirror = False
        waiting_for_caption = False
        waiting_for_new_caption = False
        waiting_for_photo = False
        waiting_for_user_ep_range = False
        ongoing_task = False
        telegram_upload = False
        self.reset()
        
           

        await message.reply_text("Task cancelled.")
    else:
        await message.reply_text("No task is ongoing.")
@app.on_callback_query(filters.regex("^th:"))
async def handle_thumb(client, callback_query):
    action = callback_query.data
    global waiting_for_photo
    global msgopt
    global msgopt_id
    global caption_view_msg
    global caption_view_msg_id
    if action == "th:view":

        doc = dbmongo.find_one()
        encoded_image = doc["thumbnail"]
        if encoded_image is not None:
            with open("thumbnail.jpg", "wb") as f:
                f.write(encoded_image)
                await client.send_photo(
                    callback_query.message.chat.id, photo="thumbnail.jpg"
                )
                await callback_query.message.edit_reply_markup(reply_markup=None)
                await app.delete_messages(callback_query.message.chat.id, msgopt_id)
    elif action == "th:delete":
        path = os.path.join(thumbpath, "thumbnail.jpg")
        filter = {}
        update = {
            "$set": {"thumbnail": None}
        }
        dbmongo.update_one(filter, update)
        if os.path.exists(path):
            os.remove(path)
            await callback_query.message.edit_reply_markup(reply_markup=None)
            await client.answer_callback_query(
                callback_query.id, "Thumbnail Deleted", show_alert=True
            )
            await app.delete_messages(callback_query.message.chat.id, msgopt.id)
        else:
            await callback_query.message.reply_text("No thumbnail to delete.")
    elif action == "th:add":
        path = os.path.join(thumbpath, "thumbnail.jpg")
        if os.path.exists(path):
            os.remove(path)
        await callback_query.message.edit_reply_markup(reply_markup=None)

        await app.delete_messages(callback_query.message.chat.id, msgopt.id)
        msgopt = await callback_query.message.reply_text("Send the thumbnail.")
        waiting_for_photo = True

    elif action == "th:caption":
        if check_caption() == 1:
            caption_keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            text="Delete caption", callback_data="csth:captiondelete"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="Edit caption", callback_data="csth:captionedit"
                        )
                    ],

                ]
            )
        else:
            caption_keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            text="Add caption", callback_data="csth:captionadd"
                        )
                    ],

                ]
            )

            try:
                if msgopt:
                    await app.delete_messages(callback_query.message.chat.id, msgopt.id)
                else:
                    print("None")
            except Exception as e:
                print(f"An error occurred: {e}")
        doc = dbmongo.find_one()
        db_caption = doc["caption"]
        if db_caption is not None:
            caption_view_msg = await callback_query.message.reply_text(
                f"<b>Caption Settings</b>\n<b>Current Caption:</b>\n{db_caption}",
                reply_markup=caption_keyboard,
                parse_mode=ParseMode.HTML,
            )
            caption_view_msg_id = caption_view_msg.id
        else:
            caption_view_msg = await callback_query.message.reply_text(
                "<b>Caption Settings:</b>", reply_markup=caption_keyboard
            )
            caption_view_msg_id = caption_view_msg.id


@app.on_callback_query(filters.regex("^csth:"))
async def handle_caption(client, callback_query):
    global waiting_for_caption
    global waiting_for_new_caption
    global send_caption_msg_id
    global caption_view_msg_id

    action = callback_query.data

    if action == "csth:captionadd":
        await callback_query.message.edit_reply_markup(reply_markup=None)
        await app.delete_messages(callback_query.message.chat.id, caption_view_msg_id)
        send_caption_msg = await callback_query.message.reply_text("Send the caption.")
        send_caption_msg_id = send_caption_msg.id
        waiting_for_caption = True
    elif action == "csth:captiondelete":
        if dbmongo is not None:
            filter = {}
            update = {
                "$set": {"caption": None}
            }
            dbmongo.update_one(filter, update)
            await callback_query.message.edit_reply_markup(reply_markup=None)
            await app.delete_messages(
                callback_query.message.chat.id, caption_view_msg_id
            )
            await client.answer_callback_query(
                callback_query.id, "Caption Deleted.", show_alert=True
            )
        else:
            print("No database connection")
    elif action == "csth:captionedit":
        await callback_query.message.edit_reply_markup(reply_markup=None)
        await app.delete_messages(callback_query.message.chat.id, caption_view_msg_id)
        send_caption_msg = await callback_query.message.reply_text(
            "Send the new caption."
        )
        send_caption_msg_id = send_caption_msg.id
        waiting_for_new_caption = True


@app.on_message(filters.text & filters.user(OWNER_ID))
async def caption_text(client, message):
    global waiting_for_caption
    global waiting_for_new_caption
    global send_caption_msg_id
    global waiting_for_user_ep_range
    global user_ep_range
    if waiting_for_caption:
        caption = message.text
        if dbmongo is not None:
            filter = {}
            update = {
                "$set": {"caption": caption}
            }
            dbmongo.update_one(filter, update)

            await app.delete_messages(message.chat.id, message.id)
            await app.delete_messages(message.chat.id, send_caption_msg_id)
            logger.info("Caption added")
        else:
            logger.error("No database connection")
        waiting_for_caption = False
    elif waiting_for_new_caption:
        caption = message.text
        if dbmongo is not None:
            filter = {}
            update = {
                "$set": {"caption": caption}
            }
            dbmongo.update_one(filter, update)
            print("Caption updated")
            await app.delete_messages(message.chat.id, message.id)
            await app.delete_messages(message.chat.id, send_caption_msg_id)
        else:
            print("No database connection")
        waiting_for_new_caption = False
    if waiting_for_user_ep_range:
        user_ep_range = message.id
        pattern = r"^(\d+(-\d+)?)(,\d+(-\d+)?)*$"
        if not re.match(pattern, message.text):
            await message.reply_text("Invalid input! Please enter a valid range.")
            return
        await bot.on_message(client, message)
        waiting_for_user_ep_range = False


@app.on_message(filters.photo & filters.user(OWNER_ID))
async def thumb_image(client, message):
    global waiting_for_photo
    global msgopt
    if waiting_for_photo:

        path = os.path.join(thumbpath, "thumbnail.jpg")
        await message.download(file_name=path)
        await message.reply_text("Thumbnail added.")

        await app.delete_messages(message.chat.id, msgopt.id)
        await app.delete_messages(message.chat.id, message.id)
        if os.path.exists(path):
            with open(path, "rb") as f:
                encoded_image = Binary(f.read())
                filter = {}
                update = {
                    "$set": {"thumbnail": encoded_image}
                }
                dbmongo.update_one(filter, update)
        waiting_for_photo = False


@app.on_callback_query(filters.regex(r"^\d+:(360|480|720|1080)$"))
async def on_callback_query_resoloution(client, callback_query):
    await bot.on_callback_query_resoloution(client, callback_query)


@app.on_callback_query(filters.regex(r"^\d+:(download_yes|download_no)$"))
async def on_callbackquery_download(client, callback_query):
    await bot.on_callbackquery_download(client, callback_query)


@app.on_callback_query()
async def on_callback_query(client, callback_query):
    if waiting_for_search_drama:
        await bot.on_callback_query(client, callback_query)
app.run()
