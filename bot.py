# -*- coding: utf-8 -*-
import discord
from discord import app_commands
from discord.ext import commands
import os
import asyncio
from dotenv import load_dotenv # 用于在本地测试时加载环境变量

# --- 配置 ---
load_dotenv() # 本地加载 .env 文件中的变量
TOKEN = os.getenv('DISCORD_TOKEN')
if not TOKEN:
    # 如果没有在环境变量或 .env 文件中找到令牌，则引发错误
    raise ValueError("环境变量 'DISCORD_TOKEN' 未设置。请在 Railway 的 Variables 或本地的 .env 文件中设置它。")

MUSIC_FOLDER = "music/" # 存放音乐文件的文件夹路径 (相对于 bot.py)

# 确保 music 文件夹存在
if not os.path.exists(MUSIC_FOLDER):
    print(f"创建音乐文件夹: {MUSIC_FOLDER}")
    os.makedirs(MUSIC_FOLDER)

# --- 机器人设置 ---
intents = discord.Intents.default()
intents.message_content = False # 斜杠命令不需要消息内容意图
intents.voice_states = True     # 需要语音状态变化意图，以便知道用户何时加入/离开频道
bot = commands.Bot(command_prefix="!", intents=intents) # 对于斜杠命令，前缀实际上不会用到

# --- 全局状态 (简单方法) ---
# 为简单起见使用全局变量；对于大型机器人，请考虑使用 Cogs/类
song_queue = asyncio.Queue() # 使用 asyncio 队列管理歌曲顺序
current_vc = None # 用于存储当前的 VoiceClient (语音连接)
bot_is_playing = asyncio.Event() # 用于标记机器人是否正在播放音乐的事件信号

# --- 辅助函数 ---

async def play_next(interaction: discord.Interaction):
    """播放队列中的下一首歌曲"""
    global current_vc, bot_is_playing
    # 检查队列是否非空、机器人是否连接到语音频道
    if not song_queue.empty() and current_vc and current_vc.is_connected():
        bot_is_playing.set() # 设置信号，表明机器人正尝试播放
        filepath = await song_queue.get() # 从队列中获取下一首歌的路径
        song_name = os.path.basename(filepath) # 获取文件名

        if os.path.exists(filepath): # 确保文件存在
            try:
                # 使用 FFmpeg 创建音频源
                # 可以在 executable 参数中指定 ffmpeg 路径，如果它不在系统 PATH 中
                # 也可以在 options 参数中添加 -filter:a "volume=0.5" 等来调整音量
                source = discord.FFmpegPCMAudio(filepath) # 如果ffmpeg不在PATH, 用 executable="path/to/ffmpeg"

                # 定义 'after' 回调函数，它会在歌曲播放完毕或出错时被调用
                def after_playing(error):
                    if error:
                        print(f'播放器错误: {error}')
                    # 在尝试播放下一首之前，清除“正在播放”信号
                    bot_is_playing.clear()
                    # 使用 bot 的事件循环来安排下一次播放检查
                    # 传递 interaction 以便后续可能发送消息
                    bot.loop.create_task(play_next(interaction))
                    # 标记队列任务完成 (无论成功或失败)
                    song_queue.task_done()

                # 开始播放，并将 after_playing 函数作为回调传入
                current_vc.play(source, after=after_playing)

                # 尝试在原始交互的频道发送“正在播放”消息
                try:
                    # 使用 followup，因为初始响应很可能是 deferred (已延迟)
                    await interaction.followup.send(f'▶️ 正在播放: **{song_name}**')
                except discord.NotFound: # 交互可能已经过期
                     print(f"无法发送 '正在播放' 消息给 {song_name} (交互可能已过期?).")
                except Exception as e:
                     print(f"发送 '正在播放' 消息时出错: {e}")

            except Exception as e:
                print(f"播放 {filepath} 时出错: {e}")
                try:
                    await interaction.followup.send(f"❌ 播放 `{song_name}` 时出错: {e}")
                except Exception as send_error:
                    print(f"发送播放错误消息时出错: {send_error}")
                bot_is_playing.clear() # 清除播放信号
                song_queue.task_done() # 标记失败的任务已完成
                # 即使在设置阶段出错，也尝试播放下一首
                bot.loop.create_task(play_next(interaction))
        else:
            # 如果文件路径无效 (例如，文件被删除)
            print(f"文件未找到，跳过: {filepath}")
            try:
                await interaction.followup.send(f"⚠️ 文件 `{song_name}` 未找到，已跳过。")
            except Exception as send_error:
                print(f"发送文件未找到消息时出错: {send_error}")
            bot_is_playing.clear() # 清除播放信号
            song_queue.task_done() # 标记跳过的任务已完成
            # 尝试播放下一首
            bot.loop.create_task(play_next(interaction))
    else:
        # 队列为空或机器人已断开连接
        bot_is_playing.clear() # 确保播放信号已关闭
        print("队列播放完毕或机器人已断开连接。")
        # （可选）当队列为空时自动离开频道
        # if current_vc and current_vc.is_connected():
        #     await asyncio.sleep(60) # 等待一段时间再离开
        #     if not bot_is_playing.is_set() and song_queue.empty(): # 再次检查状态
        #         print("空闲超时，自动离开频道。")
        #         await current_vc.disconnect()
        #         current_vc = None

async def music_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """通过搜索 music 文件夹提供自动补全选项"""
    choices = []
    try:
        # 列出 music 文件夹中的所有文件
        files = [f for f in os.listdir(MUSIC_FOLDER) if os.path.isfile(os.path.join(MUSIC_FOLDER, f))]
        # 简单的匹配逻辑：文件名包含当前输入内容（忽略大小写）
        matches = [f for f in files if current.lower() in f.lower()]

        # Discord 最多只允许 25 个选项
        for match in matches[:25]:
            # name 是显示给用户的，value 是命令实际收到的值
            choices.append(app_commands.Choice(name=match, value=match))
    except FileNotFoundError:
        print(f"自动补全错误: 音乐文件夹 '{MUSIC_FOLDER}' 未找到。")
    except Exception as e:
        print(f"自动补全过程中出错: {e}")
    return choices

# --- 机器人事件 ---

@bot.event
async def on_ready():
    """当机器人准备好并连接到 Discord 时调用"""
    print(f'以 {bot.user.name} ({bot.user.id}) 身份登录')
    print('------')
    try:
        # 同步斜杠命令到 Discord
        # 全局同步可能需要长达一小时才能生效
        synced = await bot.tree.sync()
        # 如果想只在特定服务器（测试用，速度快）同步：
        # guild_id = YOUR_SERVER_ID_HERE # 替换成你的服务器 ID (整数)
        # synced = await bot.tree.sync(guild=discord.Object(id=guild_id))
        print(f"已同步 {len(synced)} 个命令")
    except Exception as e:
        print(f"同步命令时出错: {e}")

# --- 斜杠命令 ---

@bot.tree.command(name="play", description="查找并播放本地音乐文件夹中的歌曲")
@app_commands.describe(song_name="输入歌曲名称进行搜索") # 参数描述
@app_commands.autocomplete(song_name=music_autocomplete) # 绑定自动补全函数
async def play_slash(interaction: discord.Interaction, song_name: str):
    """将歌曲添加到队列，并在需要时开始播放"""
    global current_vc
    # 立刻确认交互，防止 Discord 认为机器人无响应 (超时)
    # ephemeral=True 让这条"正在处理"的消息只有发送者可见
    await interaction.response.defer(ephemeral=False)

    # 1. 检查用户是否在语音频道
    if not interaction.user.voice:
        await interaction.followup.send("你需要先加入一个语音频道！")
        return

    channel = interaction.user.voice.channel # 获取用户所在的语音频道

    # 2. 连接或移动机器人到用户频道
    if not current_vc or not current_vc.is_connected():
        try:
            # 连接到用户所在的频道
            current_vc = await channel.connect()
            print(f"已连接到 {channel.name}")
        except discord.ClientException:
             # 如果机器人已经在别的频道了
             await interaction.followup.send("我已经连接到另一个语音频道了。")
             # 可以选择移动机器人： await current_vc.move_to(channel)
             return
        except Exception as e:
            await interaction.followup.send(f"无法加入语音频道: {e}")
            return
    elif current_vc.channel != channel:
        # 如果机器人在当前服务器的其他频道，移动过去
        try:
            await current_vc.move_to(channel)
            print(f"已移动到 {channel.name}")
        except Exception as e:
            await interaction.followup.send(f"无法移动到你的语音频道: {e}")
            return

    # 3. 查找用户选择的文件
    filepath = os.path.join(MUSIC_FOLDER, song_name) # 构建完整文件路径
    # 检查文件是否存在且确实是文件 (不是文件夹)
    if not os.path.exists(filepath) or not os.path.isfile(filepath):
        await interaction.followup.send(f"❌ 找不到歌曲文件 '{song_name}'。请确认文件名或使用自动补全功能。")
        return

    # 4. 将歌曲路径添加到队列
    await song_queue.put(filepath)
    await interaction.followup.send(f"✅ 已添加 **{song_name}** 到播放列表。当前队列长度: {song_queue.qsize()}")

    # 5. 如果当前没有歌曲在播放，则开始播放
    if not bot_is_playing.is_set(): # 使用事件信号检查是否在播放
        await play_next(interaction) # 启动播放循环

@bot.tree.command(name="stop", description="停止播放并清空播放列表")
async def stop_slash(interaction: discord.Interaction):
    """停止当前播放并清空队列"""
    global current_vc
    await interaction.response.defer()

    if current_vc and current_vc.is_connected():
        current_vc.stop() # 停止当前播放 (这会触发 after 回调, 但我们接下来会清空队列)
        # 清空队列
        count = 0
        while not song_queue.empty():
            try:
                song_queue.get_nowait() # 取出但不处理
                song_queue.task_done() # 标记完成
                count += 1
            except asyncio.QueueEmpty:
                break
        bot_is_playing.clear() # 确保播放状态已清除
        await interaction.followup.send(f"⏹️ 播放已停止，并清空了 {count} 首待播歌曲。")
    else:
        await interaction.followup.send("我当前没有在播放音乐或连接到语音频道。")

@bot.tree.command(name="skip", description="跳过当前播放的歌曲")
async def skip_slash(interaction: discord.Interaction):
    """跳过当前歌曲，播放队列中的下一首"""
    global current_vc
    await interaction.response.defer()

    if current_vc and current_vc.is_playing():
        await interaction.followup.send("⏭️ 正在跳过当前歌曲...")
        # 停止当前歌曲会触发 play_next (通过 after 回调)
        current_vc.stop()
    elif current_vc and bot_is_playing.is_set():
         # 特殊情况：可能播放出错卡住了，但信号没清除
         await interaction.followup.send("⏭️ 当前无响应，尝试强制跳过...")
         current_vc.stop() # 再次尝试停止
         bot_is_playing.clear() # 强制清除状态
         bot.loop.create_task(play_next(interaction)) # 手动触发下一首
    else:
        await interaction.followup.send("当前没有歌曲正在播放，无法跳过。")

@bot.tree.command(name="queue", description="显示当前的播放列表")
async def queue_slash(interaction: discord.Interaction):
    """显示待播放的歌曲队列"""
    # ephemeral=True 让队列信息只有发送命令的用户可见
    await interaction.response.defer(ephemeral=True)

    if song_queue.empty():
        await interaction.followup.send("播放列表是空的！")
        return

    queue_list = []
    # 注意：访问内部 _queue 通常不推荐，但这里是为了查看队列而不消耗它
    # 复制一份以防在迭代时队列被修改
    items_in_queue = list(song_queue._queue)

    for i, filepath in enumerate(items_in_queue):
        song_name = os.path.basename(filepath) # 获取文件名
        queue_list.append(f"{i+1}. {song_name}")

    if queue_list:
         # TODO: 更准确地显示当前正在播放的歌曲
         # 需要在 play_next 开始播放时记录当前歌曲名
         message = "**播放列表:**\n" + "\n".join(queue_list)
         # Discord 消息长度限制约为 2000 字符
         if len(message) > 1900:
              message = message[:1900] + "\n... (列表过长)"
         await interaction.followup.send(message)
    else:
         # 理论上如果 items_in_queue 非空，这里不会执行
         await interaction.followup.send("播放列表是空的！")


@bot.tree.command(name="leave", description="让机器人离开语音频道")
async def leave_slash(interaction: discord.Interaction):
    """断开机器人与语音频道的连接"""
    global current_vc
    await interaction.response.defer()

    if current_vc and current_vc.is_connected():
        # 在离开前停止播放并清空队列
        current_vc.stop()
        count = 0
        while not song_queue.empty():
            try:
                song_queue.get_nowait()
                song_queue.task_done()
                count += 1
            except asyncio.QueueEmpty:
                break
        bot_is_playing.clear()

        await current_vc.disconnect() # 断开连接
        current_vc = None # 清除 VoiceClient 引用
        print(f"已断开连接，并清空了 {count} 首待播歌曲。")
        await interaction.followup.send("👋 已离开语音频道。")
    else:
        await interaction.followup.send("我不在任何语音频道中。")


# --- 运行机器人 ---
if __name__ == "__main__":
    if TOKEN == "YOUR_ACTUAL_DISCORD_BOT_TOKEN_HERE" or TOKEN is None:
         print("错误：请在 .env 文件或环境变量中设置 DISCORD_TOKEN！")
    else:
        print("正在启动机器人...")
        bot.run(TOKEN)