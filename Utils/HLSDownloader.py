__author__ = 'Prudhvi PLN'

import os
import re

from Utils.commons import retry
from Utils.BaseDownloader import BaseDownloader
#from bot import app
from Utils.logger import logger

class HLSDownloader(BaseDownloader):
    '''Download Client for HLS files'''
    # References: https://github.com/Oshan96/monkey-dl/blob/master/anime_downloader/util/hls_downloader.py
    # https://github.com/josephcappadona/m3u8downloader/blob/master/m3u8downloader/m3u8.py

    def __init__(self, dl_config, referer_link, out_file, session=None):
        # initialize base downloader
        super().__init__(dl_config, referer_link, out_file, session)
        # initialize HLS specific configuration
        self.m3u8_file = os.path.join(f'{self.temp_dir}', 'uwu.m3u8')
        self.thread_name_prefix = 'udb-m3u8-'

    def _has_uri(self, m3u8_data):
        method = re.search('URI=(.*)', m3u8_data)
        if method is None: return False
        if method.group(1) == "NONE": return False

        return True

    def _collect_uri_iv(self, m3u8_data):
        # Case-1: typical HLS using URI & IV
        uri_iv = re.search('#EXT-X-KEY:METHOD=AES-128,URI="(.*)",IV=(.*)', m3u8_data)

        # Case-2: typical HLS using URI only
        if uri_iv is None:
            uri_data = re.search('URI="(.*)"', m3u8_data)
            return uri_data.group(1), None

        uri = uri_iv.group(1)
        iv = uri_iv.group(2)

        return uri, iv

    def _collect_ts_urls(self, m3u8_link, m3u8_data):
        # Improved regex to handle all cases. (get all lines except those starting with #)
        base_url = '/'.join(m3u8_link.split('/')[:-1])
        normalize_url = lambda url, base_url: (url if url.startswith('http') else base_url + '/' + url)
        urls = [ normalize_url(url.group(0), base_url) for url in re.finditer("^(?!#).+$", m3u8_data, re.MULTILINE) ]

        return urls

    @retry()
    def _download_segment(self, ts_url):
        '''
        download segment file from url. Reuse if already downloaded.

        Returns: (download_status, progress_bar_increment)
        '''
        try:
            segment_file_nm = ts_url.split('/')[-1]
            segment_file = os.path.join(f"{self.temp_dir}", f"{segment_file_nm}")

            # check if the segment is already downloaded
            if os.path.isfile(segment_file) and os.path.getsize(segment_file) > 0:
                return (f'Segment file [{segment_file_nm}] already exists. Reusing.', 1)

            with open(segment_file, "wb") as ts_file:
                ts_file.write(self._get_stream_data(ts_url))

            return (f'Segment file [{segment_file_nm}] downloaded', 1)

        except Exception as e:
            return (f'\nERROR: Segment download failed [{segment_file_nm}] due to: {e}', 0)

    def _rewrite_m3u8_file(self, m3u8_data):
        # regex safe temp dir path
        seg_temp_dir = self.temp_dir.replace('\\', '\\\\')
        # ffmpeg doesn't accept backward slash in key file irrespective of platform
        key_temp_dir = self.temp_dir.replace('\\', '/')
        with open(self.m3u8_file, "w") as m3u8_f:
            m3u8_content = re.sub('URI=(.*)/', f'URI="{key_temp_dir}/', m3u8_data, count=1)
            regex_safe = '\\\\' if os.sep == '\\' else '/'
            m3u8_content = re.sub(r'https://(.*)/', f'{seg_temp_dir}{regex_safe}', m3u8_content)
            m3u8_f.write(m3u8_content)

    def _convert_to_mp4(self):
        # print(f'Converting {self.out_file} to mp4')
        out_file = os.path.join(f'{self.out_dir}', f'{self.out_file}')
        cmd = f'ffmpeg -loglevel warning -allowed_extensions ALL -i "{self.m3u8_file}" -c copy -bsf:a aac_adtstoasc "{out_file}"'
        self._exec_cmd(cmd)

    def start_download(self, m3u8_link):
        # create output directory
        #logger.debug('Creating output directories')
        self._create_out_dirs()

        iv = None
        #logger.debug('Fetching stream data')
        m3u8_data = self._get_stream_data(m3u8_link, True)

        #logger.debug('Check if stream is encrypted/mapped')
        if self._has_uri(m3u8_data):
            #logger.debug('Stream is encrypted/mapped. Collect iv data and download key')
            key_uri, iv = self._collect_uri_iv(m3u8_data)
            status = self._download_segment(key_uri)
            if status[1] == 0: logger.error(f'Failed to download key/map file with error: {status[0]}')

        # did not run into HLS with IV during development, so skipping it
        if iv:
            raise Exception("Current code cannot decode IV links")

        #logger.debug('Collect .ts segment urls')
        ts_urls = self._collect_ts_urls(m3u8_link, m3u8_data)

        #logger.debug('Downloading collected .ts segments')
        metadata = {
            'type': 'segments',
            'total': len(ts_urls),
            'unit': 'seg'
        }
        self._multi_threaded_download(self._download_segment, ts_urls, **metadata)

        #logger.debug('Rewrite m3u8 file with downloaded .ts segments paths')
        self._rewrite_m3u8_file(m3u8_data)

        #logger.debug('Converting .ts files to .mp4')
        self._convert_to_mp4()

        # remove temp dir once completed and dir is empty
        #logger.debug('Removing temporary directories')
        self._remove_out_dirs()

        return (0, 'Success')
