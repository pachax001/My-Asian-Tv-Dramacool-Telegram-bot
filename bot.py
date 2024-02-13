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
last_update_time = 0
waiting_for_photo = False

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


handler = logging.FileHandler('bot.log')
handler.setLevel(logging.DEBUG)


stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.DEBUG)


formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
stream_handler.setFormatter(formatter)


logger.addHandler(handler)
logger.addHandler(stream_handler)


config_file = "config_udb.yaml"
config = load_yaml(config_file)
downloader_config = config['DownloaderConfig']
max_parallel_downloads = downloader_config['max_parallel_downloads']
thumbpath = downloader_config['thumbpath']

ep_range_msg = None
search_res_msg = None
select_res_msg = None

proceed_msg = None
user_ep_range = None

def is_file_in_directory(filename, directory):
    return 1 if os.path.isfile(os.path.join(directory, filename)) else 0

if not os.path.exists(downloader_config['download_dir']):
    print(f"Creating download directory:{downloader_config['download_dir']}...")
    os.makedirs(downloader_config['download_dir'])
if not os.path.exists(thumbpath):
    print(f"Creating thumbnail directory:{thumbpath}...")
    os.makedirs(thumbpath)
#load_dotenv()
load_dotenv('config.env', override=True)
BOT_TOKEN = os.getenv('BOT_TOKEN')

OWNER_ID = int(os.getenv('OWNER_ID'))

API_ID = os.getenv('API_ID')

API_HASH = os.getenv('API_HASH')

app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

    
#app.run(set_commands(app))   
def get_resolutions(items):
    '''
    genarator function to yield the resolutions of available episodes
    '''
    for item in items:
        yield [ i for i in item.keys() if i not in ('error', 'original') ]

def downloader(ep_details, dl_config):
    '''
    download function where Download Client initialization and download happens.
    Accepts two dicts: download config, episode details. Returns download status.
    '''
    # load color themes
    

    get_current_time = lambda fmt='%F %T': datetime.now().strftime(fmt)
    start = get_current_time()
    start_epoch = int(time())

    out_file = ep_details['episodeName']

    if 'downloadLink' not in ep_details:
        return f'[{start}] Download skipped for {out_file}, due to error: {ep_details.get("error", "Unknown")}'

    download_link = ep_details['downloadLink']
    download_type = ep_details['downloadType']
    referer = ep_details['refererLink']
    out_dir = dl_config['download_dir']

    # create download client for the episode based on type
    if download_type == 'hls':
        logger.debug(f'Creating HLS download client for {out_file}')
        from Utils.HLSDownloader import HLSDownloader
        dlClient = HLSDownloader(dl_config, referer, out_file)

    elif download_type == 'mp4':
        logger.debug(f'Creating MP4 download client for {out_file}')
        from Utils.BaseDownloader import BaseDownloader
        dlClient = BaseDownloader(dl_config, referer, out_file)

    else:
        return 3,f'[{start}] Download skipped for {out_file}, due to unknown download type [{download_type}]'
    logger.debug(f'Download started for {out_file}...')
    logger.info(f'Download started for {out_file}...')

    if os.path.isfile(os.path.join(f'{out_dir}', f'{out_file}')):
        # skip file if already exists
        return 0, f'[{start}] Download skipped for {out_file}. File already exists!'
        
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
            return 1,f'[{end}] Download failed for {out_file}, with error: {msg}'

        end_epoch = int(time())
        download_time = pretty_time(end_epoch-start_epoch, fmt='h m s')
        return 2,f'[{end}] Download completed for {out_file} in {download_time}!'

def batch_downloader(download_fn, links, dl_config, max_parallel_downloads):

    @threaded(max_parallel=max_parallel_downloads, thread_name_prefix='udb-', print_status=False)
    def call_downloader(link, dl_config):
        result=download_fn(link, dl_config)
        print ("results from batch-downloader",result)
        return result

    dl_status = call_downloader(links.values(), dl_config)
    print("dl_status",dl_status)
    # show download status at the end, so that progress bars are not disturbed
    print("\033[K") # Clear to the end of line
    #width = os.get_terminal_size().columns
    

   
    status_str = f'Download Summary:'
    for status in dl_status:
        status_str += f'\n{status}'
    # Once chatGPT suggested me to reduce 'print' usage as it involves IO to stdout
    print(status_str)
    # strip ANSI before writing to log file
    logger.info((status_str))
    return dl_status

class DramaBot:
    def __init__(self, config):
        self.DCL = DramaClient(config['drama'])
        self.default_ep_range = "1-16"
        self.reset()
    def reset(self):
        self.waiting_for_ep_range = False
        self.ep_range = None
        self.ep_start = None
        self.ep_end = None
        self.target_series = None
        self.episode_links = None
        self.ep_infos = None
        self.target_dl_links = None
        self.series_title = None
        self.episode_prefix = None
        self.search_results_message_id = None
        self.search_id = int(_time.time())
        self.search_results = {}
    async def drama(self, client, message):
        global search_res_msg
        self.reset()        
        keyword = ' '.join(message.command[1:])
        try:            
            search_results = self.DCL.search(keyword)
            self.search_results = {i + 1: result for i, result in enumerate(search_results.values())}
        except Exception as e:
            print(f"An error occurred during search: {e}")
            await message.reply_text("An error occurred during the search.")
            return
        if not self.search_results:
            await message.reply_text("No results found.")
            return
        keyboard = [[InlineKeyboardButton(f"{result['title']} ({result['year']})", callback_data=f"{self.search_id}:{i+1}")] for i, result in enumerate(self.search_results.values())]
        reply_markup = InlineKeyboardMarkup(keyboard)
        search_res_msg = await message.reply_text("Search Results:", reply_markup=reply_markup)
        
        
    async def on_callback_query(self, client, callback_query):
        
        global search_res_msg
        search_id, series_index = map(int, callback_query.data.split(':'))        
        if search_id != self.search_id:       
            await client.answer_callback_query(callback_query.id, "Cannot perform search on previous results.", show_alert=True)
            return
        await callback_query.message.edit_reply_markup(reply_markup=None)
        await app.delete_messages(callback_query.message.chat.id, search_res_msg.id)
        self.episode_links = None
        self.ep_infos = None
        self.target_dl_links = None
        self.series_title = None
        self.episode_prefix = None
        logger.debug(f'{series_index = }')
        self.target_series = self.search_results[series_index]
        title = self.target_series['title']
        logger.debug(f'{title= }')
        episodes = self.DCL.fetch_episodes_list(self.target_series)
        
        episodes_message = self.DCL.show_episode_results(episodes, (self.ep_start, self.ep_end))
        global ep_message_ids
        ep_message_ids = [] 
        if episodes_message:
            messages = [episodes_message[i:i + 4096] for i in range(0, len(episodes_message), 4096)]
            for message in messages:
                logger.debug("Getting episodes")
                ep_msg = await callback_query.message.reply_text(message)
                ep_message_ids.append(ep_msg.id)
        else:
            await callback_query.message.reply_text("No episodes found.")
        
        await self.get_ep_range(client, callback_query.message, 'Enter', None)
    async def on_message(self, client, message):
        #global ep_msg_id
        global ep_message_ids
        global ep_range_msg
        for ep_msg_id in ep_message_ids:
            await app.delete_messages(message.chat.id, ep_msg_id)
        await app.delete_messages(message.chat.id, ep_range_msg.id)
        if self.waiting_for_ep_range:
            self.waiting_for_ep_range = False
            self.ep_range = message.text or "all"
            if str(self.ep_range).lower() == 'all':
                self.ep_range = self.default_ep_range
                self.mode = 'all'
            else:
                self.mode = 'custom'
            logger.debug(f'Selected episode range ({self.mode = }): {self.ep_range = }')
            try:
                self.ep_start, self.ep_end = map(float, self.ep_range.split('-'))
            except ValueError as ve:
                self.ep_start = self.ep_end = float(self.ep_range)

            episodes = self.DCL.fetch_episodes_list(self.target_series)
            await self.show_episode_links(client, message, episodes, self.ep_start, self.ep_end)
    
    async def show_episode_links(self, client, message, episodes, ep_start, ep_end):
        global select_res_msg
        global ep_infos_msg_id
        self.episode_links,self.ep_infos = self.DCL.fetch_episode_links(episodes, ep_start, ep_end)
        await app.delete_messages(message.chat.id, user_ep_range)
        ep_infos_msg_id = []
        for info in self.ep_infos:
            print('ep info',info)
            ep_info_msg = await message.reply_text(info)
            ep_infos_msg_id.append(ep_info_msg.id)
        valid_resolutions = []
        valid_resolutions_gen = get_resolutions(self.episode_links.values())
        for _valid_res in valid_resolutions_gen:
            valid_resolutions = _valid_res
            if len(valid_resolutions) > 0:
                break   
        else:
            valid_resolutions = ['360','480','720','1080']
        #logger.debug(f'Set output names based on {self.target_series['title']}')
        self.series_title, self.episode_prefix = self.DCL.set_out_names(self.target_series)
        #logger.debug(f'{self.series_title = }, {self.episode_prefix = }')
        downloader_config['download_dir'] = os.path.join(f"{downloader_config['download_dir']}", f"{self.series_title}")
        logger.debug(f"Final download dir: {downloader_config['download_dir']}")
        logger.debug(f'{valid_resolutions = }')
        keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(text=res, callback_data=f"{self.search_id}:{res}")] for res in valid_resolutions]
    )
        select_res_msg=await message.reply_text("Please select a resolution:", reply_markup=keyboard)
    
    async def get_ep_range(self, client, message, mode='Enter', _episodes_predef=None):
        global ep_range_msg
        if _episodes_predef:
            self.ep_range = _episodes_predef
            try:
                self.ep_start, self.ep_end = map(float, self.ep_range.split('-'))
            except ValueError as ve:
                self.ep_start = self.ep_end = float(self.ep_range)
        else:
            ep_range_msg=await message.reply_text(f"\n{mode} episodes to download (ex: 1-16): ")
            self.waiting_for_ep_range = True

    async def on_callback_query_resoloution(self,client,callback_query):
        global ep_details_msg_ids
        global select_res_msg
        global ep_infos_msg_id
        ep_details_msg_ids = []
        global proceed_msg
        await callback_query.message.edit_reply_markup(reply_markup=None)
        await app.delete_messages(callback_query.message.chat.id, select_res_msg.id)
        #print('ep_details_msg_ids',ep_details_msg_ids)
        #print('select_res_msg',select_res_msg.id)
        for ep_info_msg_id in ep_infos_msg_id:
            #print('ep_info_msg_id',ep_info_msg_id)
            await app.delete_messages(callback_query.message.chat.id, ep_info_msg_id)
        search_id,resint = map(int, callback_query.data.split(':'))
        resolution = str(resint)
        if search_id != self.search_id:
            await client.answer_callback_query(callback_query.id, "Cannot select resolution on previous results.", show_alert=True)
            return
        self.target_dl_links = self.DCL.fetch_m3u8_links(self.episode_links, resolution,self.episode_prefix)
        for ep, details in self.target_dl_links.items():
            episode_name = details['episodeName']
            episode_subs = details['episodeSubs']
            ep_details_msg = await callback_query.message.reply_text(f"Episode {ep}:\nName: {episode_name}\nSubs: {episode_subs}")
            ep_details_msg_ids.append(ep_details_msg.id)
        available_dl_count = len([ k for k, v in self.target_dl_links.items() if v.get('downloadLink') is not None ])
        logger.debug('Links Found!!')
        msg = f'Episodes available for download [{available_dl_count}/{len(self.target_dl_links)}].Proceed to download?'
        keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Yes", callback_data=f"{self.search_id}:download_yes"), InlineKeyboardButton("No", callback_data=f"{self.search_id}:download_no")]
        ]
    )   
       
        proceed_msg = await callback_query.message.reply_text(msg,reply_markup=keyboard) 
        if len(self.target_dl_links) == 0:
            logger.error('No episodes available to download! Exiting.')
            await callback_query.message.reply_text('No episodes available to download! Exiting.')
            await callback_query.message.edit_reply_markup(reply_markup=None)
            return
        #await callback_query.message.edit_reply_markup(reply_markup=None)

    async def on_callbackquery_download(self, client, callback_query):
        #global ep_details_msg_ids
        search_id, action = callback_query.data.split(':')
        int_search_id = int(search_id)
        if int_search_id != self.search_id:
            await client.answer_callback_query(callback_query.id, "Cannot download previous selections", show_alert=True)
            return
        if callback_query.data == f"{self.search_id}:download_yes":
            await callback_query.message.edit_reply_markup(reply_markup=None)
            await app.delete_messages(callback_query.message.chat.id, proceed_msg.id)
            
            for ep_details_msg_id in ep_details_msg_ids:
                print('ep_details_msg_id',ep_details_msg_id)
                await app.delete_messages(callback_query.message.chat.id, ep_details_msg_id)
            start_msg =await callback_query.message.reply_text("Downloading episodes...")
            logger.debug("Downloading episodes...")
            download_results=batch_downloader(downloader, self.target_dl_links, downloader_config, max_parallel_downloads)
            for status, message in download_results:
                if status == 0:#File Already Exist
                    await asyncio.sleep(1)
                    await callback_query.message.reply_text(message)
                elif status == 2: #Download complete
                    await callback_query.message.reply_text(message)
                elif status == 3:#Unknown Download Type
                    await callback_query.message.reply_text(message)
                elif status == 1:#Download Failed
                    await callback_query.message.reply_text(message)
            await client.delete_messages(callback_query.message.chat.id, start_msg.id)  # Delete the start message

            directory = downloader_config['download_dir']
            print(f"Downloaded files are saved in {directory}")
            print("Files are being sent to the user...")
            async def progress(current, total, message):
                global last_update_time
                if _time.time() - last_update_time >5:
                    #global upload_prog_msg
                    await message.edit_text(f"Upload progress: {current * 100 / total:.1f}%")
                    last_update_time = _time.time()
                    await asyncio.sleep(1)
            try:
                
                message = await client.send_message(callback_query.message.chat.id, "Starting upload...")
                for filename in os.listdir(directory):
                    filepath = os.path.join(directory, filename)
                    if os.path.isfile(filepath) and filename.endswith('.mp4'):
                        
                        thumbnail = os.path.join(thumbpath, 'thumbnail.jpg')
                        if os.path.exists(thumbnail):   
                            await client.send_document(callback_query.from_user.id, document=filepath, progress=progress, progress_args=(message,),thumb=thumbnail,)
                        else:
                            await client.send_document(callback_query.from_user.id, document=filepath, progress=progress, progress_args=(message,))
                await app.delete_messages(callback_query.message.chat.id, message.id)            
            except Exception as e:
                print(f"An error occurred while sending files: {e}")
            try:
                #print(f"Deleted {directory}")
                shutil.rmtree(directory)
                
            except Exception as e:
                print(f"An error occurred while deleting {directory}: {e}")
                      
            #print('downloader message',downloader)
        else:
            await callback_query.message.reply_text("Download cancelled.")
            await callback_query.message.edit_reply_markup(reply_markup=None)
            await app.delete_messages(callback_query.message.chat.id, proceed_msg.id)
            for ep_details_msg_id in ep_details_msg_ids:
                await app.delete_messages(callback_query.message.chat.id, ep_details_msg_id)
            self.target_dl_links = None
            self.target_series = None
            self.search_results = {}
            logger.debug("Download cancelled.")
        
        
bot = DramaBot(config)
msgpot = None
msgopt_id = None

@app.on_message(filters.command("start",prefixes="/"))
async def start(client, message):
    await app.set_bot_commands([
    BotCommand("start", "Start the bot"),
    BotCommand("drama", "Search and download dramas"),
    BotCommand("thumbset", "Set thumbnail for the bot")])
    #await set_commands(client)
    #print(set_commands(client))
    if message.from_user.id != OWNER_ID:
        keyboard=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(text='Owner', url='https://t.me/gunaya001')],
                [InlineKeyboardButton(text='Join Kdrama Request Group', url='https://t.me/kdramasmirrorchat')],
                [InlineKeyboardButton(text='Join Ongoing Kdrama Channel', url='https://t.me/kdramasmirrorlog')],
                [InlineKeyboardButton(text='Bot Repo', url='https://t.me/kdramasmirrorlog2')],
            ]
        )
        await message.reply_text("You are not authorized to use this bot.", reply_markup=keyboard)
        return
    elif message.from_user.id == OWNER_ID:
        await message.reply_text("Download Dramas From https://myasiantv.ac/\n\nUse /drama {drama name} to search for a drama.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(text='Owner', url='https://t.me/gunaya001')]]))
    #await message.reply_text("Download Dramas From https://myasiantv.ac/\n\nUse /drama {drama name} to search for a drama.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(text='Owner', url='https://t.me/gunaya001')]]))

option_thumb_keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(text='View thumbnail', callback_data='th:view')],
                [InlineKeyboardButton(text='Delete thumbnail', callback_data='th:delete')],
                [InlineKeyboardButton(text='Change', callback_data='th:add')]
                
                
            ]
        )
add_thumb_keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(text='Add thumbnail', callback_data='th:add')]
            ]
        )

@app.on_message(filters.command("thumbset") & filters.user(OWNER_ID))
async def thumb_without_reply(client, message):
    result = is_file_in_directory('thumbnail.jpg', thumbpath)
    #print(result)
    global msgopt
    global msgopt_id
    if result == 1:
        #global msgopt
        msgopt = await message.reply_text("Choose option ", reply_markup=option_thumb_keyboard)
        print(msgopt.id)
        msgopt_id = msgopt.id
    else:
        #global msgopt
        msgopt = await message.reply_text("No thumbnail to view or delete.", reply_markup=add_thumb_keyboard)
@app.on_callback_query(filters.regex("^th:"))
async def handle_thumb(client, callback_query):
    action =  callback_query.data
    global waiting_for_photo
    global msgopt
    global msgopt_id
    if action == 'th:view':
        path = os.path.join(thumbpath, 'thumbnail.jpg')
        if os.path.exists(path):
            
            
            await client.send_photo(callback_query.message.chat.id, photo=path)
            await callback_query.message.edit_reply_markup(reply_markup=None)
            
            await app.delete_messages(callback_query.message.chat.id, msgopt_id)
            
            
           
        
   
        
    elif action == 'th:delete':
        path = os.path.join(thumbpath, 'thumbnail.jpg')
        if os.path.exists(path):
            os.remove(path)
            await callback_query.message.edit_reply_markup(reply_markup=None)
            await client.answer_callback_query(callback_query.id, "Thumbnail Deleted", show_alert=True)
            await app.delete_messages(callback_query.message.chat.id, msgopt.id)
            
            

        else:
            await callback_query.message.reply_text("No thumbnail to delete.")
    elif action == 'th:add':
        path = os.path.join(thumbpath, 'thumbnail.jpg')
        if os.path.exists(path):
            os.remove(path)
        await callback_query.message.edit_reply_markup(reply_markup=None)
        #global msgopt
        await app.delete_messages(callback_query.message.chat.id, msgopt.id)
        msgopt = await callback_query.message.reply_text("Send the thumbnail.")
        waiting_for_photo = True
        print(waiting_for_photo)
            

@app.on_message(filters.photo & filters.user(OWNER_ID))
async def thumb_image(client, message):
    global waiting_for_photo
    global msgopt
    if waiting_for_photo:
        print(waiting_for_photo)
        path = os.path.join(thumbpath, 'thumbnail.jpg')
        await message.download(file_name=path)
        await message.reply_text("Thumbnail added.")
        #global msgopt
        await app.delete_messages(message.chat.id, msgopt.id)
        await app.delete_messages(message.chat.id, message.id)
        waiting_for_photo = False
        #await callback_query.message.edit_reply_markup(reply_markup=option_thumb_keyboard)               



@app.on_message(filters.command("drama") & filters.user(OWNER_ID))
async def drama(client, message):
    await bot.drama(client, message)
@app.on_callback_query(filters.regex(r'^\d+:(360|480|720|1080)$'))
async def on_callback_query_resoloution(client, callback_query):
    await bot.on_callback_query_resoloution(client, callback_query)
@app.on_callback_query(filters.regex(r'^\d+:(download_yes|download_no)$'))
async def on_callbackquery_download(client, callback_query):
    await bot.on_callbackquery_download(client, callback_query)
@app.on_callback_query()
async def on_callback_query(client, callback_query):
    await bot.on_callback_query(client, callback_query)
@app.on_message(filters.text & filters.user(OWNER_ID))
async def on_message(client, message):
    global user_ep_range
    user_ep_range = message.id
    if '-' in message.text:
        parts = message.text.split('-')
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            return
    elif not message.text.isdigit():
        return
    await bot.on_message(client, message)
app.run()


