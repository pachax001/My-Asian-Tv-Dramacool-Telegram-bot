# My-Asian_TV-Dramacool-Telegram-Bot


## This Bot can download shows and movies from  [MyAsianTv](https://myasiantv.ac/). 
### Base code is from [UDB](https://github.com/Prudhvi-pln/udb).
## Features ✅
Can Download episode range for a drama.<br>
Can set captions and Thumnails for medias.<br>
## ❗Limitations ❗
Only can download one movie or a series at one time.<br>
If Multiple Episode are selected all episodes will be downloaded simultaneously but one episode after episode will be uploaded.<br>
No progress for download. But will send a message saying the episode dowloaded.<br>
Don't send multiple commands while a process is ongoing.<br>
Thumbnail and caption feature will only work if the MongoDB is connected.(Recommended to add this)<br>
Only the owner can use the bot.
All files are send as documents.
## Installation

### Deploy in your vps(Linux)

- Clone this repo:
```
git clone https://github.com/pachax001/My-Asian-Tv-Dramacool-Telegram-bot  && cd My-Asian-Tv-Dramacool-Telegram-bot
```
Install Docker by following the [official Docker docs](https://docs.docker.com/engine/install/debian/)

Fill the variables in [config.env](https://github.com/pachax001/My-Asian-Tv-Dramacool-Telegram-bot/blob/main/config.env)
<br> [Click here](https://github.com/pachax001/My-Asian-Tv-Dramacool-Telegram-bot/blob/main/README.md#configs) for more info on config. </br>

- There are two methods to build and run the docker:
  1. Using official docker commands.
  2. Using docker-compose. (Recommended)

------

#### Build And Run The Docker Image Using Official Docker Commands

- Start Docker daemon (SKIP if already running, mostly you don't need to do this):

```
sudo dockerd
```

- Build Docker image:

```
sudo docker build . -t wzmlx
```

- Run the image:

```
sudo docker run -p 80:80 -p 8080:8080 wzmlx
```

- To stop the running image:

```
sudo docker ps
```

```
sudo docker stop id
```

----

#### Build And Run The Docker Image Using docker-compose



- Install docker-compose

```
sudo apt install docker-compose
```

- Build and run Docker image or to view current running image:

```
sudo docker-compose up
```

- After editing files with nano for example (nano start.sh):

```
sudo docker-compose up --build
```

- To stop the running image:

```
sudo docker-compose stop
```

- To run the image:

```
sudo docker-compose start
```

- To get latest log from already running image (after mounting the folder):

```
sudo docker-compose up
```
------

#### Docker Notes

**IMPORTANT NOTES**:

1. You should stop the running image before deleting the container and you should delete the container before the image.
2. To delete the container (this will not affect on the image):

```
sudo docker container prune
```

3. To delete the images:

```
sudo docker image prune -a
```

## Configs
### config.env file
* BOT_TOKEN     - Get bot token from @BotFather

* APP_ID        - From my.telegram.org (or @UseTGXBot)

* API_HASH      - From my.telegram.org (or @UseTGXBot)

* OWNER_ID      - Your Telegram ID. Get from send /id command to @MissRose_bot

* DATABASE_URL  - MongoDB URL ([Click here](https://github.com/pachax001/My-Asian-Tv-Dramacool-Telegram-bot/blob/main/README.md#-generate-mongodb-database) for more info on MongoDB URL.) </br>

### config_udb.yaml file
#### Warning ⚠
##### It is recommend to keep default settings as usual
* alternate_resolution_selector: - To select the next quality. For example if this field is set to lowest and youchoose the download quality as 720p and if 720p is not available in some episodes this option will trigger and download the next lowest quality episode.
* max_parallel_downloads: Max parallel downloads. Recommended value is 1.
#### ❗Do not Change other Values ❗

### 🤖 ***Bot Commands***
```
start - Start the bot
drama - Search for drama or movie
usetting - Settings for caption and thumbnail
```
### 📡 ***Generate MongoDB Database***

1. Go to `https://mongodb.com/` and sign-up.
2. Create Shared Cluster.
3. Press on `Database` under `Deployment` Header, your created cluster will be there.
5. Press on connect, choose `Allow Acces From Anywhere` and press on `Add IP Address` without editing the ip, then create user.
6. After creating user press on `Choose a connection`, then press on `Connect your application`. Choose `Driver` **python** and `version` **3.6 or later**.
7. Copy your `connection string` and replace `<password>` with the password of your user, then press close.

## 🏅 **Credits**
|<img width="80" src="https://avatars.githubusercontent.com/u/62585477">|<img width="80" src="https://avatars.githubusercontent.com/u/34474300">|

|[`Prudhvi-pln`](https://github.com/tbdsux)|[`Pyrogram`](https://github.com/pyrogram)|
<br>|Creator of UDB|Telegram Bot Framework|</br>

