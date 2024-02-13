__author__ = 'Prudhvi PLN'

import logging
import os
import re
import requests
import sys
import yaml
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import wraps
from time import sleep
from logging.handlers import RotatingFileHandler
from subprocess import Popen, PIPE
import logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Create a file handler
handler = logging.FileHandler('bot.log')
handler.setLevel(logging.DEBUG)

# Create a stream handler (this will print log messages to the console)
stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.DEBUG)

# Create a logging format
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
stream_handler.setFormatter(formatter)

# Add the handlers to the logger
logger.addHandler(handler)
logger.addHandler(stream_handler)


# strip ANSI characters, to write to log file
strip_ansi = lambda text: re.sub(r'\x1b\[[0-9;]*m', '', text)





def exec_os_cmd(cmd):
    '''
    Execute any OS commands
    Args: command to be executed
    Returns: output of executed command
    '''
    proc = Popen(cmd, stdout=PIPE, stderr=PIPE, shell=True)
    # print stdout to console
    msg = proc.communicate()[0].decode("utf-8")
    std_err = proc.communicate()[1].decode("utf-8")
    rc = proc.returncode
    if rc != 0:
        raise Exception(f"Error occured: {std_err}")
    return msg



# display seconds in hh mm ss format
def pretty_time(sec: int, fmt='hh:mm:ss'):
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 3600 % 60
    if fmt == 'hh:mm:ss':
        return '{:02d}:{:02d}:{:02d}'.format(h,m,s)
    else:
        return '{:02d}h {:02d}m {:02d}s'.format(h,m,s) if h > 0 else '{:02d}m {:02d}s'.format(m,s)


# custom stdout printer


# custom decorator for retring of a function
def retry(exceptions=(Exception,), tries=3, delay=2, backoff=2, print_errors=False):
    """
    Retry Decorator
    Retries the wrapped function/method `times` times if the exceptions listed
    in ``exceptions`` are thrown
    :param Exceptions: Lists of exceptions that trigger a retry attempt
    :type Exceptions: Tuple of Exceptions
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            attempt, mdelay = 0, delay
            while attempt < tries:
                try:
                    return_status = func(*args, **kwargs)
                    if type(return_status) == tuple and return_status[1] == 0:
                        raise Exception(return_status)
                    return return_status
                except exceptions as e:
                    # colprint('error', f'{e} | Attempt: {attempt} / {tries}')
                    sleep(mdelay)
                    attempt += 1
                    mdelay *= backoff
                    if attempt >= tries and print_errors:
                        logger.debug('error', f'{e} | Final Attempt: {attempt} / {tries}')
            return func(*args, **kwargs)
        return wrapper
    return decorator

# custom decorator to make any function multi-threaded
def threaded(max_parallel=None, thread_name_prefix='udb-', print_status=False):
    '''
    make any function multi-threaded by adding this decorator
    '''
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            final_status = []
            results = {}
            # Using a with statement to ensure threads are cleaned up promptly
            with ThreadPoolExecutor(max_workers=max_parallel, thread_name_prefix=thread_name_prefix) as executor:

                futures = { executor.submit(func, i, *args[1:], **kwargs): idx for idx, i in enumerate(args[0]) }

                for future in as_completed(futures):
                    i = futures[future]
                    try:
                        # store result
                        data = future.result()
                        # if 'completed' not in data:
                        #     print(data)
                        if print_status: print(f"\033[F\033[K\r{data}")
                        results[i] = data
                    except Exception as e:
                       logger.debug('error', f'{e}')

            # sort the results in same order as received
            for idx, status in sorted(results.items()):
                final_status.append(status)

            return final_status
        return wrapper
    return decorator

# load yaml config into dict
def load_yaml(config_file):
    if not os.path.isfile(config_file):
        logger.debug('error', f'Config file [{config_file}] not found')
        exit(1)

    with open(config_file, "r") as stream:
        try:
            return yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            logger.debug('error', f"Error occured while reading yaml file: {exc}")
            exit(1)

# custom logging formatter to highlight error messages
class CustomLogFormatter(logging.Formatter):
    '''A Formatter to highlight error log level messages in red'''
    def __init__(self, fmt=None, datefmt=None, style='%', validate=True):
        super().__init__(fmt, datefmt, style, validate)

    def format(self, record):
        if record.levelname == 'ERROR':
            record.msg = f'["error"]{record.msg}["reset"]'
        return super().format(record)

# custom logger function
def create_logger(**logger_config):
    '''Create a logging handler

    Args: logging configuration as a dictionary [Allowed keys: log_level, log_dir, log_filename, max_log_size_in_kb, log_backup_count]
    Returns: a logging handler'''
    # human-readable log-level to logging.* mapping
    log_levels = {
        'DEBUG': logging.DEBUG,
        'INFO': logging.INFO,
        'WARNING': logging.WARNING,
        'ERROR': logging.ERROR
    }

    # set default logging configuration
    default_logging_config = {
        'log_level': 'INFO',
        'log_dir': 'log',
        'log_filename': 'udb.log',
        'max_log_size_in_kb': 1000,
        'log_backup_count': 3
    }

    # update missing logging configuration with defaults
    for key in default_logging_config.keys():
        if key not in logger_config:
            logger_config[key] = default_logging_config[key]

    # format the log entries
    file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(funcName)s:%(lineno)s - %(message)s')
    stdout_formatter = CustomLogFormatter()
    # get root logger
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    # create logging directory
    if logger_config['log_dir']: os.makedirs(logger_config['log_dir'], exist_ok=True)

    # create logging handler for stdout
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(stdout_formatter)
    stdout_handler.setLevel(logging.ERROR)

    # add rotating file handler to rotate log file when size crosses a threshold
    file_handler = RotatingFileHandler(
        os.path.join(logger_config['log_dir'], logger_config['log_filename']),
        maxBytes = logger_config['max_log_size_in_kb'] * 1000,  # KB to Bytes
        backupCount = logger_config['log_backup_count'],
        encoding='utf-8'
    )
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(log_levels.get(logger_config['log_level'].upper()))

    logger.addHandler(file_handler)     # print to file
    logger.addHandler(stdout_handler)   # print only error to stdout

    return logger
