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
# import pymongo
# from pymongo import MongoClient, errors
from urllib.parse import urlparse
from db import usersettings_collection as dbmongo
from bson.binary import Binary
import re

# from moviepy.editor import VideoFileClip
import math
from pymediainfo import MediaInfo

last_update_time = 0
waiting_for_photo = False
waiting_for_caption = False
waiting_for_search_drama = False
waiting_for_new_caption = False
waiting_for_user_ep_range = False
waiting_for_download_complete = False
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
base_client = BaseClient()
# print(base_client.udb_episode_dict)
# caption = document["caption"]
# print(caption)
# print(document)

handler = logging.FileHandler("bot.log")
handler.setLevel(logging.DEBUG)


stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.DEBUG)


formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
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
        # caption = document["caption"]
        if document is not None:
            caption = document["caption"]
            # print(caption)

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
    print(f"Creating download directory:{downloader_config['download_dir']}...")
    os.makedirs(downloader_config["download_dir"])
if not os.path.exists(thumbpath):
    print(f"Creating thumbnail directory:{thumbpath}...")
    os.makedirs(thumbpath)
# load_dotenv()
load_dotenv("config.env", override=True)
BOT_TOKEN = os.getenv("BOT_TOKEN")

OWNER_ID = int(os.getenv("OWNER_ID"))

API_ID = os.getenv("API_ID")

API_HASH = os.getenv("API_HASH")


app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, max_concurrent_transmissions=16)


# app.run(set_commands(app))
def get_resolutions(items):
    """
    genarator function to yield the resolutions of available episodes
    """
    for item in items:
        yield [i for i in item.keys() if i not in ("error", "original")]


def downloader(ep_details, dl_config):
    global waiting_for_download_complete
    waiting_for_download_complete = False
    """
    download function where Download Client initialization and download happens.
    Accepts two dicts: download config, episode details. Returns download status.
    """
    # load color themes

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

    # create download client for the episode based on type
    if download_type == "hls":
        logger.debug(f"Creating HLS download client for {out_file}")
        from Utils.HLSDownloader import HLSDownloader

        dlClient = HLSDownloader(dl_config, referer, out_file)

    elif download_type == "mp4":
        logger.debug(f"Creating MP4 download client for {out_file}")
        from Utils.BaseDownloader import BaseDownloader

        dlClient = BaseDownloader(dl_config, referer, out_file)

    else:
        return (
            3,
            f"[{start}] Download skipped for {out_file}, due to unknown download type [{download_type}]",
        )
    logger.debug(f"Download started for {out_file}...")
    logger.info(f"Download started for {out_file}...")

    if os.path.isfile(os.path.join(f"{out_dir}", f"{out_file}")):
        # skip file if already exists
        return 0, f"[{start}] Download skipped for {out_file}. File already exists!"

    else:
        try:
            # main function where HLS download happens
            status, msg = dlClient.start_download(download_link)
        except Exception as e:
            status, msg = 1, str(e)

        # remove target dirs if no files are downloaded
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
    print("dl_status", dl_status)
    # show download status at the end, so that progress bars are not disturbed
    print("\033[K")  # Clear to the end of line
    # width = os.get_terminal_size().columns

    status_str = f"Download Summary:"
    for status in dl_status:
        status_str += f"\n{status}"
    # Once chatGPT suggested me to reduce 'print' usage as it involves IO to stdout
    print(status_str)
    # strip ANSI before writing to log file
    logger.info((status_str))
    return dl_status


class DramaBot:
    def __init__(self, config):
        self.DCL = DramaClient(config["drama"])
        self.default_ep_range = "1-16"
        self.reset()
        self.semaphore = asyncio.Semaphore(16)

    def reset(self):
        # print("Reset method called")
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

    async def drama(self, client, message):
        # print(base_client.udb_episode_dict)
        self.DCL.udb_episode_dict.clear()
        base_client.udb_episode_dict.clear()
        # print(self.target_dl_links)
        global search_res_msg
        global waiting_for_search_drama
        self.reset()
        keyword = " ".join(message.command[1:])
        if not keyword.strip():
            await message.reply_text("No search keyword provided.")
            return
        try:
            search_results = self.DCL.search(keyword)
            # print("search reslut type",type(search_results))
            self.search_results = {
                i + 1: result for i, result in enumerate(search_results.values())
            }
        except Exception as e:
            print(f"An error occurred during search: {e}")
            await message.reply_text("An error occurred during the search.")
            return
        if not self.search_results:
            await message.reply_text("No results found.")
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

    async def on_callback_query(self, client, callback_query):

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
        logger.debug(f"{series_index = }")
        self.target_series = self.search_results[series_index]
        # print('target_series type',type(self.target_series))
        title = self.target_series["title"]
        logger.debug(f"{title= }")
        episodes = self.DCL.fetch_episodes_list(self.target_series)
        # print('episodes type',type(episodes))
        episodes_message = self.DCL.show_episode_results(
            episodes, (self.ep_start, self.ep_end)
        )
        global ep_message_ids
        ep_message_ids = []
        if episodes_message:
            messages = [
                episodes_message[i : i + 4096]
                for i in range(0, len(episodes_message), 4096)
            ]
            for message in messages:
                logger.debug("Getting episodes")
                ep_msg = await callback_query.message.reply_text(message)
                ep_message_ids.append(ep_msg.id)
        else:
            await callback_query.message.reply_text("No episodes found.")

        await self.get_ep_range(client, callback_query.message, "Enter", None)
        waiting_for_search_drama = False

    async def on_message(self, client, message):
        # global ep_msg_id
        global ep_message_ids
        global ep_range_msg
        for ep_msg_id in ep_message_ids:
            await app.delete_messages(message.chat.id, ep_msg_id)
        await app.delete_messages(message.chat.id, ep_range_msg.id)
        if self.waiting_for_ep_range:
            self.waiting_for_ep_range = False
            self.ep_range = message.text or "all"
            if str(self.ep_range).lower() == "all":
                self.ep_range = self.default_ep_range
                self.mode = "all"
            else:
                self.mode = "custom"
            logger.debug(f"Selected episode range ({self.mode = }): {self.ep_range = }")
            if self.ep_range.count("-") > 1:
                logger.error("Invalid input! You must specify only one range.")
                return  # get_ep_range(default_ep_range, mode, _episodes_predef)

            self.ep_start, self.ep_end, self.specific_eps = 0, 0, []
            for ep_range in self.ep_range.split(","):
                if "-" in ep_range:  # process the range if '-' is found
                    ep_range = ep_range.split("-")
                    if ep_range[0] == "":
                        ep_range[0] = self.default_ep_range.split("-")[
                            0
                        ]  # if not set, set episode start to default start number
                    if ep_range[1] == "":
                        ep_range[1] = self.default_ep_range.split("-")[
                            1
                        ]  # if not set, set episode end to default end number

                    self.ep_start, self.ep_end = map(float, ep_range)
                else:
                    self.specific_eps.append(
                        float(ep_range)
                    )  # if '-' is not found, then it's a specific episode number

            episodes = self.DCL.fetch_episodes_list(self.target_series)
            # print('episodes type',type(episodes))
            await self.show_episode_links(
                client, message, episodes, self.ep_start, self.ep_end, self.specific_eps
            )

    async def show_episode_links(
        self, client, message, episodes, ep_start, ep_end, specific_eps
    ):
        global select_res_msg
        global ep_infos_msg_id
        message_patience = await message.reply_text("Fetching episode links, Be patience😊😊😊...")
        self.episode_links, self.ep_infos = self.DCL.fetch_episode_links(
            episodes, ep_start, ep_end, specific_eps
        )
        # print('episode_links',self.episode_links)
        # print('ep_infos',self.ep_infos)
        await app.delete_messages(message.chat.id, user_ep_range)
        await app.delete_messages(message.chat.id, message_patience.id)
        info_text = '\n'.join(self.ep_infos)
        info_texts = [info_text[i:i+4096] for i in range(0, len(info_text), 4096)]
        ep_infos_msg_id = []
        for info in info_texts:
            # print('ep info',info)
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
        # logger.debug(f'Set output names based on {self.target_series['title']}')
        self.series_title, self.episode_prefix = self.DCL.set_out_names(
            self.target_series
        )
        # print('series_title',self.series_title)
        # print('episode_prefix',self.episode_prefix)
        # logger.debug(f'{self.series_title = }, {self.episode_prefix = }')
        downloader_config["download_dir"] = os.path.join(
            f"{downloader_config['download_dir']}", f"{self.series_title}"
        )
        # logger.debug(f"Final download dir: {downloader_config['download_dir']}")
        logger.debug(f"{valid_resolutions = }")
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
            # try:
            #     self.ep_start, self.ep_end = map(float, self.ep_range.split("-"))
            # except ValueError as ve:
            #     self.ep_start = self.ep_end = float(self.ep_range)
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
        await callback_query.message.edit_reply_markup(reply_markup=None)
        await app.delete_messages(callback_query.message.chat.id, select_res_msg.id)
        # print('ep_details_msg_ids',ep_details_msg_ids)
        # print('select_res_msg',select_res_msg.id)
        for ep_info_msg_id in ep_infos_msg_id:
            # print('ep_info_msg_id',ep_info_msg_id)
            await app.delete_messages(callback_query.message.chat.id, ep_info_msg_id)
        search_id, resint = map(int, callback_query.data.split(":"))
        resolution = str(resint)
        # print('search_id',search_id)
        # print('resolution',resolution)
        if search_id != self.search_id:
            await client.answer_callback_query(
                callback_query.id,
                "Cannot select resolution on previous results.",
                show_alert=True,
            )
            return
        #print("episode_links", self.episode_links)
        self.target_dl_links = self.DCL.fetch_m3u8_links(
            self.episode_links, resolution, self.episode_prefix
        )
        #print("target_dl_links", self.target_dl_links)
        all_ep_details_text = ""
        for ep, details in self.target_dl_links.items():
            episode_name = details["episodeName"]
            episode_subs = details["episodeSubs"]
            ep_details_text = f"Episode {ep}:\nName: {episode_name}\nSubs: {episode_subs}"
            all_ep_details_text += ep_details_text
        all_ep_details_texts = [all_ep_details_text[i:i+4096] for i in range(0, len(all_ep_details_text), 4096)]
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
        logger.debug("Links Found!!")
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
            return
        # await callback_query.message.edit_reply_markup(reply_markup=None)

    async def on_callbackquery_download(self, client, callback_query):
        # semaphore = asyncio.Semaphore(16)

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
                #print(f"Uploading {document} with {os.getpid()}")
                filename = os.path.basename(document)
                logger.debug(f"Uploading {filename} with {os.getpid()}")
                await asyncio.sleep(1)
                message = await client.send_message(chat_id, f"Starting upload of {filename}")
                try:
                    return await client.send_document(
                        chat_id,
                        document=document,
                        progress=progress,
                        progress_args=(message,*progress_args,),
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
            await app.delete_messages(callback_query.message.chat.id, proceed_msg.id)

            for ep_details_msg_id in ep_details_msg_ids:
                # print('ep_details_msg_id',ep_details_msg_id)
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
            for status, message in download_results:
                if status == 0:  # File Already Exist
                    await asyncio.sleep(1)
                    await callback_query.message.reply_text(message)
                elif status == 2:  # Download complete
                    await callback_query.message.reply_text(message)
                elif status == 3:  # Unknown Download Type
                    await callback_query.message.reply_text(message)
                elif status == 1:  # Download Failed
                    await callback_query.message.reply_text(message)
            await client.delete_messages(
                callback_query.message.chat.id, start_msg.id
            )  # Delete the start message

            directory = downloader_config["download_dir"]

            # print(f"Downloaded files are saved in {directory}")
            # print("Files are being sent to the user...")
            last_update_times = {}

            async def progress(current, total, message, filename):
                now = _time.time()
                progress_text = f"{filename} : {current * 100 / total:.1f}%"
                if message.text != progress_text:
                    if filename not in last_update_times or now - last_update_times[filename] > 10:
                        try:
                            await message.edit_text(progress_text)
                            last_update_times[filename] = now
                        except pyrogram.errors.exceptions.bad_request_400.MessageNotModified:
                            pass
                await asyncio.sleep(1)

            try:

                # message = await client.send_message(
                #     callback_query.message.chat.id, "Starting upload..."
                # )
                #print("Starting upload...")
                upload_tasks = []
                for filename in os.listdir(directory):
                    filepath = os.path.join(directory, filename)
                    if os.path.isfile(filepath) and filename.endswith(".mp4"):
                        media_info = MediaInfo.parse(filepath)
                        for track in media_info.tracks:
                            print(
                                f"Track type: {track.track_type}, Duration: {track.duration}"
                            )
                            if track.track_type == "Video":
                                milliseconds = track.duration
                                if milliseconds is not None:
                                    seconds, milliseconds = divmod(milliseconds, 1000)
                                    minutes, seconds = divmod(seconds, 60)
                                    hours, minutes = divmod(minutes, 60)
                                    if hours > 0:
                                        duration = f"{hours}h{minutes}m{seconds}s"
                                    elif minutes > 0:
                                        duration = f"{minutes}m{seconds}s"
                                    else:
                                        duration = (
                                            f"{seconds}s"  # duration in milliseconds
                                        )
                                else:
                                    duration = "Unknown"
                                file_size = os.path.getsize(filepath)
                                print(f"File size: {file_size}")  # file size in bytes
                                file_size_con = convert_size(file_size)
                                break
                            # convert_size(file_size)

                        doc = dbmongo.find_one()
                        encoded_image = doc["thumbnail"]
                        caption_db = doc["caption"]
                        try:
                            caption_db.format(
                                filename="test", size="test", duration="test"
                            )
                        except KeyError as e:
                            print(f"Caption contains unrecognized format: {e}")
                            await app.send_message(
                                callback_query.message.chat.id,
                                f"Caption contains unrecognized format: {e}. Removing the format.",
                            )
                            caption_db = re.sub(r"\{[^}]*\}", "", caption_db)
                        if encoded_image is not None and caption_db is None:
                            with open("thumbnail.jpg", "wb") as f:
                                f.write(encoded_image)
                                task = send_document(
                                    client,
                                    callback_query.from_user.id,
                                    document=filepath,
                                    progress=progress,
                                    progress_args=(os.path.basename(filename),),
                                    thumb="thumbnail.jpg",
                                )
                                upload_tasks.append(asyncio.create_task(task))
                        elif encoded_image is None and caption_db is None:
                            task = send_document(
                                client,
                                callback_query.from_user.id,
                                document=filepath,
                                progress=progress,
                                progress_args=(os.path.basename(filename),),
                            )
                            upload_tasks.append(asyncio.create_task(task))
                        elif encoded_image is not None and caption_db is not None:
                            file_name = html.escape(os.path.basename(filepath))
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
                                    progress_args=(os.path.basename(filename),),
                                    thumb="thumbnail.jpg",
                                    caption=caption,
                                    parse_mode=ParseMode.HTML,
                                )
                                upload_tasks.append(asyncio.create_task(task))
                        elif encoded_image is None and caption_db is not None:
                            file_name = html.escape(os.path.basename(filepath))
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
                                progress_args=(os.path.basename(filename),),
                                caption=caption,
                                parse_mode=ParseMode.HTML,
                            )
                            upload_tasks.append(asyncio.create_task(task))
                            # ... previous code where you add tasks to upload_tasks ...

                            # Start the uploads and handle them as they complete

                try:
                    await asyncio.gather(*upload_tasks)
                except Exception as e:
                    print(f"An error occurred while sending files: {e}")
                    await app.send_message(
                        callback_query.message.chat.id,
                        f"An error occurred while sending files.{e}",
                    )

                    # ... rest of your code ...
                # await asyncio.gather(*upload_tasks)        #await app.delete_messages(callback_query.message.chat.id, message.id)
                self.target_dl_links = {}
                self.target_series = None
                self.search_results = {}
            except Exception as e:
                print(f"An error occurred while sending files: {e}")
                await app.send_message(
                    callback_query.message.chat.id,
                    "An error occurred while sending files.",
                )
            finally:
                if os.path.exists(directory):
                    try:
                        print(f"Deleted {directory}")
                        shutil.rmtree(directory)
                        await app.send_message(
                            callback_query.message.chat.id,
                            "All Episodes Uploaded.",
                        )
                    except Exception as e:
                        print(f"An error occurred while deleting {directory}: {e}")

            # print("downloader message", downloader)
        else:
            await callback_query.message.reply_text("Download cancelled.")
            await callback_query.message.edit_reply_markup(reply_markup=None)
            await app.delete_messages(callback_query.message.chat.id, proceed_msg.id)
            for ep_details_msg_id in ep_details_msg_ids:
                await app.delete_messages(
                    callback_query.message.chat.id, ep_details_msg_id
                )
            self.target_dl_links = {}
            self.target_series = None
            self.search_results = {}
            logger.debug("Download cancelled.")


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
                "usetting",
                "can use /us also.Set thumbnail and caption for uploaded media",
            ),
            BotCommand("help", "Get help"),
        ]
    )
    if message.from_user.id != OWNER_ID:
        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(text="Owner", url="https://t.me/gunaya001")],
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
            "Download Dramas From https://myasiantv.ac/\n\nUse /drama {drama name} to search for a drama.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(text="Owner", url="https://t.me/gunaya001")]]
            ),
        )

        # await message.reply_text("Download Dramas From https://myasiantv.ac/\n\nUse /drama {drama name} to search for a drama.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(text='Owner', url='https://t.me/gunaya001')]]))


@app.on_message(filters.command("drama") & filters.user(OWNER_ID))
async def drama(client, message):
    # print("TRiggered")
    await bot.drama(client, message)


@app.on_message(filters.command("help") & filters.user(OWNER_ID))
async def help(client, message):
    await message.reply_text(
        "Download Dramas From https://myasiantv.ac/\n\nUse /drama {drama name} to search for a drama.\n\nUse /usetting to set thumbnail and caption for uploaded media.\nCaption has filterings for {filename}, {size}, {duration}.\n\nUse {filename} to display the filename, {size} to display the file size and {duration} to display the duration of the video in caption.\n\nCaption also supports HTML formatting.\n\nHTML formattings can be found here https://core.telegram.org/bots/api#html-style",
        disable_web_page_preview=True,
    )


@app.on_message(
    (filters.command("usetting") | filters.command("us")) & filters.user(OWNER_ID)
)
async def thumb_without_reply(client, message):
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
            # largest_photo = message.reply_to_message.photo[-1]
            # photo_id=message.reply_to_message.photo.file_id
            await client.download_media(
                message.reply_to_message.photo.file_id, file_name=path
            )
            if os.path.exists(path):
                with open(path, "rb") as f:
                    encoded_image = Binary(f.read())
                    filter = {}  # this is an empty filter that matches all documents
                    update = {
                        "$set": {"thumbnail": encoded_image}
                    }  # this update removes the 'caption' field from the matched document
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
            filter = {}  # this is an empty filter that matches all documents
            update = {
                "$set": {"caption": caption}
            }  # this update removes the 'caption' field from the matched document
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
    # global msgopt
    # await app.delete_messages(message.chat.id, msgopt.id)
    # await app.delete_messages(message.chat.id, message.id)
    # if os.path.exists(path):
    #     with open(path, "rb") as f:
    #         encoded_image = Binary(f.read())
    #         filter = {}  # this is an empty filter that matches all documents
    #         update = {
    #             "$set": {"thumbnail": encoded_image}
    #         }  # this update removes the 'caption' field from the matched document
    #         dbmongo.update_one(filter, update)
    result = is_thumb_in_db()
    keyboard_buttons_thumb = [
        [InlineKeyboardButton(text="View thumbnail", callback_data="th:view")],
        [InlineKeyboardButton(text="Delete thumbnail", callback_data="th:delete")],
        [InlineKeyboardButton(text="Change Thumbnail", callback_data="th:add")],
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
    # check_caption()
    # print(check_caption())

    # print(result)
    global msgopt
    global msgopt_id
    doc = dbmongo.find_one()
    db_caption = doc["caption"]
    if db_caption is not None:
        caption = db_caption
    else:
        caption = None
    if result == 0:

        # global msgopt
        msgopt = await message.reply_text(
            f"**Choose option**\n**Caption:** `{caption}`",
            reply_markup=no_thumb_keyboard,
        )
        # print(msgopt.id)
        msgopt_id = msgopt.id
    else:
        # global msgopt

        msgopt = await message.reply_text(
            f"**Choose option**\n**Caption is:** `{caption}`",
            reply_markup=with_thumb_keyboard,
            parse_mode=ParseMode.MARKDOWN,
        )
        msgopt_id = msgopt.id


@app.on_callback_query(filters.regex("^th:"))
async def handle_thumb(client, callback_query):
    action = callback_query.data
    global waiting_for_photo
    global msgopt
    global msgopt_id
    global caption_view_msg
    global caption_view_msg_id
    if action == "th:view":
        # path = os.path.join(thumbpath, 'thumbnail.jpg')
        # if os.path.exists(path):
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
        filter = {}  # this is an empty filter that matches all documents
        update = {
            "$set": {"thumbnail": None}
        }  # this update removes the 'caption' field from the matched document
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
        # global msgopt
        await app.delete_messages(callback_query.message.chat.id, msgopt.id)
        msgopt = await callback_query.message.reply_text("Send the thumbnail.")
        waiting_for_photo = True
        # print(waiting_for_photo)
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
                    # [InlineKeyboardButton(text='Back', callback_data='csth:back')]
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
                    # [InlineKeyboardButton(text='Back', callback_data='csth:back')]
                ]
            )
            # print(msgopt.id)

            # await callback_query.message.edit_reply_markup(reply_markup=None)
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
    # print("Triggered")
    action = callback_query.data
    # print(action)
    if action == "csth:captionadd":
        await callback_query.message.edit_reply_markup(reply_markup=None)
        await app.delete_messages(callback_query.message.chat.id, caption_view_msg_id)
        send_caption_msg = await callback_query.message.reply_text("Send the caption.")
        send_caption_msg_id = send_caption_msg.id

        waiting_for_caption = True
    elif action == "csth:captiondelete":
        if dbmongo is not None:
            filter = {}  # this is an empty filter that matches all documents
            update = {
                "$set": {"caption": None}
            }  # this update removes the 'caption' field from the matched document
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
            filter = {}  # this is the filter that specifies which document to update
            update = {
                "$set": {"caption": caption}
            }  # this is the update that sets the new value for the 'caption' field
            dbmongo.update_one(filter, update)
            # print(f"One document inserted with id {result.inserted_id}")
            await app.delete_messages(message.chat.id, message.id)
            await app.delete_messages(message.chat.id, send_caption_msg_id)
            print("Caption added")
        else:
            print("No database connection")
        waiting_for_caption = False
    elif waiting_for_new_caption:
        caption = message.text
        print(caption)
        if dbmongo is not None:
            filter = {}  # this is the filter that specifies which document to update
            update = {
                "$set": {"caption": caption}
            }  # this is the update that sets the new value for the 'caption' field
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
        # print(waiting_for_photo)
        path = os.path.join(thumbpath, "thumbnail.jpg")
        await message.download(file_name=path)
        await message.reply_text("Thumbnail added.")

        # global msgopt
        await app.delete_messages(message.chat.id, msgopt.id)
        await app.delete_messages(message.chat.id, message.id)
        if os.path.exists(path):
            with open(path, "rb") as f:
                encoded_image = Binary(f.read())
                filter = {}  # this is an empty filter that matches all documents
                update = {
                    "$set": {"thumbnail": encoded_image}
                }  # this update removes the 'caption' field from the matched document
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
