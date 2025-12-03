import discord
from discord import app_commands
from discord.ext import commands, tasks
import yt_dlp as youtube_dl
import asyncio
from collections import deque
import os
import time
import random
from datetime import datetime
import logging

# ═══════════════════════════════════════════════════════════════════════════════
#                    CORD TITAN V3 - ULTIMATE MUSIC BOT
# ═══════════════════════════════════════════════════════════════════════════════

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('CordTitan')

intents = discord.Intents.all()
bot = commands.Bot(command_prefix=['!', '?', '.', 'ct!'], intents=intents, help_command=None)

# ═══════════════════════════════════════════════════════════════════════════════
#                              YOUTUBE DL CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': False,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    'extract_flat': False,
    'socket_timeout': 30,  # Add this
    'retries': 3,  # Add this
}

ffmpeg_base_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin'
}

AUDIO_FILTERS = {
    'normal': '-vn',
    'bassboost': '-vn -af "bass=g=10,dynaudnorm=f=200"',
    'nightcore': '-vn -af "asetrate=44100*1.25,aresample=44100,bass=g=5"',
    'vaporwave': '-vn -af "asetrate=44100*0.8,aresample=44100,atempo=1.1"',
    'treble': '-vn -af "treble=g=5,dynaudnorm=f=200"',
    'superbass': '-vn -af "bass=g=20,dynaudnorm=f=150"',
    '8d': '-vn -af "apulsator=hz=0.08"',
    'karaoke': '-vn -af "stereotools=mlev=0.03"',
    'soft': '-vn -af "lowpass=f=1000,volume=0.5"',
    'loud': '-vn -af "volume=2.0,dynaudnorm=f=100"'
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

# Global storage
music_queues = {}
dj_roles = {}
mode_247 = {}

bot_stats = {
    'songs_played': 0,
    'commands_used': 0,
    'uptime_start': time.time(),
    'servers': 0,
    'total_playtime': 0
}

# ═══════════════════════════════════════════════════════════════════════════════
#                              AUDIO SOURCE CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class YTDLSource(discord.PCMVolumeTransformer):
    """Enhanced audio source with filters and metadata"""
    
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title', 'Unknown')
        self.url = data.get('url')
        self.webpage_url = data.get('webpage_url')
        self.duration = data.get('duration', 0)
        self.thumbnail = data.get('thumbnail')
        self.uploader = data.get('uploader', 'Unknown')
        self.views = data.get('view_count', 0)
        self.likes = data.get('like_count', 0)
        self.upload_date = data.get('upload_date')
        self.description = data.get('description', '')[:200]
        self.requester = None
        self.audio_filter = 'normal'

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=True, requester=None, audio_filter='normal'):
        """Create audio source from URL with optional audio filter"""
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        
        if 'entries' in data:
            data = data['entries'][0]
        
        filename = data['url'] if stream else ytdl.prepare_filename(data)
        
        filter_options = AUDIO_FILTERS.get(audio_filter, AUDIO_FILTERS['normal'])
        ffmpeg_options = {
            'options': filter_options,
            **ffmpeg_base_options
        }
        
        source = cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)
        source.requester = requester
        source.audio_filter = audio_filter
        return source

    async def recreate(self, audio_filter=None):
        """Recreate the audio source with optional new filter"""
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(self.webpage_url, download=False))
        
        if 'entries' in data:
            data = data['entries'][0]
        
        use_filter = audio_filter or self.audio_filter
        filter_options = AUDIO_FILTERS.get(use_filter, AUDIO_FILTERS['normal'])
        ffmpeg_options = {
            'options': filter_options,
            **ffmpeg_base_options
        }
        
        new_source = YTDLSource(discord.FFmpegPCMAudio(data['url'], **ffmpeg_options), data=data, volume=self.volume)
        new_source.requester = self.requester
        new_source.audio_filter = use_filter
        return new_source

    def format_duration(self):
        """Format duration as MM:SS or HH:MM:SS"""
        if not self.duration:
            return "LIVE"
        
        hours, remainder = divmod(int(self.duration), 3600)
        minutes, seconds = divmod(remainder, 60)
        
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

# ═══════════════════════════════════════════════════════════════════════════════
#                              MUSIC QUEUE CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class MusicQueue:
    """Advanced music queue with all features"""
    
    def __init__(self, guild_id):
        self.guild_id = guild_id
        self.queue = deque()
        self.history = deque(maxlen=100)
        self.current = None
        self.loop_mode = 'off'
        self.shuffle_enabled = False
        self.volume = 50
        self.play_count = 0
        self.original_queue = deque()
        self.audio_filter = 'normal'
        self.votes_skip = set()
        self.skip_threshold = 0.5
        self.last_activity = time.time()
        self.now_playing_message = None
        self.text_channel = None

    def add(self, item):
        self.queue.append(item)
        self.last_activity = time.time()

    def add_next(self, item):
        self.queue.appendleft(item)
        self.last_activity = time.time()

    def add_playlist(self, items):
        self.queue.extend(items)
        self.last_activity = time.time()

    def next(self):
        self.votes_skip.clear()
        
        if self.loop_mode == 'song' and self.current:
            return self.current
        
        if self.current and self.loop_mode != 'song':
            self.history.append(self.current)
        
        if not self.queue and self.loop_mode == 'queue' and self.original_queue:
            self.queue = deque(self.original_queue)
        
        if self.queue:
            if self.shuffle_enabled:
                index = random.randint(0, len(self.queue) - 1)
                self.current = list(self.queue)[index]
                del self.queue[index]
            else:
                self.current = self.queue.popleft()
            
            if self.loop_mode == 'queue' and self.current:
                if self.current not in self.original_queue:
                    self.original_queue.append(self.current)
            
            self.play_count += 1
            self.last_activity = time.time()
            return self.current
        
        if self.loop_mode != 'song':
            self.current = None
        return None

    def previous(self):
        if self.history:
            if self.current:
                self.queue.appendleft(self.current)
            self.current = self.history.pop()
            self.last_activity = time.time()
            return self.current
        return None

    def clear(self):
        self.queue.clear()
        self.original_queue.clear()
        self.current = None
        self.votes_skip.clear()

    def remove(self, index):
        if 0 <= index < len(self.queue):
            removed = list(self.queue)[index]
            del self.queue[index]
            return removed
        return None

    def move(self, from_pos, to_pos):
        if 0 <= from_pos < len(self.queue) and 0 <= to_pos < len(self.queue):
            queue_list = list(self.queue)
            song = queue_list.pop(from_pos)
            queue_list.insert(to_pos, song)
            self.queue = deque(queue_list)
            return song
        return None

    def skipto(self, index):
        if 0 <= index < len(self.queue):
            for _ in range(index):
                skipped = self.queue.popleft()
                self.history.append(skipped)
            return self.queue[0] if self.queue else None
        return None

    def is_empty(self):
        return len(self.queue) == 0

    def get_queue_list(self):
        return list(self.queue)

    def total_duration(self):
        total = sum(song.duration or 0 for song in self.queue)
        if self.current and self.current.duration:
            total += self.current.duration
        return total

def get_queue(guild_id):
    if guild_id not in music_queues:
        music_queues[guild_id] = MusicQueue(guild_id)
    return music_queues[guild_id]

# ═══════════════════════════════════════════════════════════════════════════════
#                              EMBED HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def create_embed(title, description, color=0x9b59b6):
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.utcnow()
    )
    embed.set_footer(text="Cord Titan V3", icon_url="https://i.imghippo.com/files/KVj9306xLE.png")
    return embed

def create_music_embed(player, action="Now Playing", queue=None):
    colors = {
        'Now Playing': 0xe91e63,
        'Added to Queue': 0x3f51b5,
        'Playing Previous': 0xff9800,
        'Looping Song': 0x9c27b0,
        'Song Info': 0x00bcd4
    }
    
    embed = discord.Embed(
        title=action,
        description=f"**[{player.title}]({player.webpage_url})**",
        color=colors.get(action, 0x9b59b6)
    )
    
    if player.thumbnail:
        embed.set_thumbnail(url=player.thumbnail)
    
    duration_str = player.format_duration()
    embed.add_field(name="Duration", value=f"`{duration_str}`", inline=True)
    embed.add_field(name="Channel", value=f"`{player.uploader}`", inline=True)
    embed.add_field(name="Requested by", value=player.requester.mention if player.requester else "Unknown", inline=True)
    
    if player.views:
        embed.add_field(name="Views", value=f"`{player.views:,}`", inline=True)
    
    if player.audio_filter != 'normal':
        embed.add_field(name="Filter", value=f"`{player.audio_filter.upper()}`", inline=True)
    
    if queue:
        loop_icons = {'off': 'Off', 'song': 'Song', 'queue': 'Queue'}
        status = f"Loop: `{loop_icons[queue.loop_mode]}` | "
        status += f"Shuffle: `{'ON' if queue.shuffle_enabled else 'OFF'}` | "
        status += f"Vol: `{queue.volume}%`"
        embed.add_field(name="Settings", value=status, inline=False)
    
    embed.set_footer(text="Cord Titan V3", icon_url="https://i.imghippo.com/files/KVj9306xLE.png")
    return embed

def format_queue_duration(seconds):
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m {secs}s"

# ═══════════════════════════════════════════════════════════════════════════════
#                              UPDATE NOW PLAYING
# ═══════════════════════════════════════════════════════════════════════════════

async def update_now_playing(guild_id):
    """Update the now playing message with current settings"""
    queue = get_queue(guild_id)
    if queue.now_playing_message and queue.current:
        try:
            embed = create_music_embed(queue.current, "Now Playing", queue)
            await queue.now_playing_message.edit(embed=embed)
        except discord.NotFound:
            queue.now_playing_message = None
        except discord.HTTPException:
            pass

# ═══════════════════════════════════════════════════════════════════════════════
#                              DJ PERMISSION CHECK
# ═══════════════════════════════════════════════════════════════════════════════

def check_dj_permission(interaction: discord.Interaction) -> bool:
    if interaction.user.guild_permissions.administrator:
        return True
    
    if interaction.guild_id in dj_roles:
        dj_role_id = dj_roles[interaction.guild_id]
        if any(role.id == dj_role_id for role in interaction.user.roles):
            return True
        return False
    
    return True

async def dj_check(interaction: discord.Interaction) -> bool:
    if not check_dj_permission(interaction):
        embed = create_embed("DJ Only", "This command requires DJ permissions!", 0xf44336)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return False
    return True

# ═══════════════════════════════════════════════════════════════════════════════
#                              UI COMPONENTS
# ═══════════════════════════════════════════════════════════════════════════════

class MusicControlView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="Pause", style=discord.ButtonStyle.primary, custom_id="pause_resume")
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await dj_check(interaction):
            return
            
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.pause()
            button.label = "Resume"
            button.style = discord.ButtonStyle.success
            await interaction.response.edit_message(view=self)
        elif vc and vc.is_paused():
            vc.resume()
            button.label = "Pause"
            button.style = discord.ButtonStyle.primary
            await interaction.response.edit_message(view=self)
        else:
            await interaction.response.send_message("Nothing playing!", ephemeral=True)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary, custom_id="skip")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await dj_check(interaction):
            return
            
        if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
            interaction.guild.voice_client.stop()
            await interaction.response.send_message("Skipped!", ephemeral=True)
        else:
            await interaction.response.send_message("Nothing playing!", ephemeral=True)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, custom_id="previous")
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await dj_check(interaction):
            return
            
        queue = get_queue(self.guild_id)
        if queue.history:
            if interaction.guild.voice_client:
                interaction.guild.voice_client.stop()
            await interaction.response.send_message("Playing previous!", ephemeral=True)
        else:
            await interaction.response.send_message("No previous song!", ephemeral=True)

    @discord.ui.button(label="Vol+", style=discord.ButtonStyle.success, custom_id="vol_up")
    async def volume_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        queue = get_queue(self.guild_id)
        if queue.volume < 200:
            queue.volume = min(200, queue.volume + 10)
            if interaction.guild.voice_client and interaction.guild.voice_client.source:
                interaction.guild.voice_client.source.volume = queue.volume / 100
            await interaction.response.send_message(f"Volume: {queue.volume}%", ephemeral=True)
            await update_now_playing(self.guild_id)
        else:
            await interaction.response.send_message("Max volume!", ephemeral=True)

    @discord.ui.button(label="Vol-", style=discord.ButtonStyle.success, custom_id="vol_down")
    async def volume_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        queue = get_queue(self.guild_id)
        if queue.volume > 0:
            queue.volume = max(0, queue.volume - 10)
            if interaction.guild.voice_client and interaction.guild.voice_client.source:
                interaction.guild.voice_client.source.volume = queue.volume / 100
            await interaction.response.send_message(f"Volume: {queue.volume}%", ephemeral=True)
            await update_now_playing(self.guild_id)
        else:
            await interaction.response.send_message("Min volume!", ephemeral=True)

    @discord.ui.button(label="Loop", style=discord.ButtonStyle.secondary, custom_id="loop_song")
    async def loop_song(self, interaction: discord.Interaction, button: discord.ui.Button):
        queue = get_queue(self.guild_id)
        if queue.loop_mode == 'off':
            queue.loop_mode = 'song'
            await interaction.response.send_message("Looping current song", ephemeral=True)
        elif queue.loop_mode == 'song':
            queue.loop_mode = 'queue'
            queue.original_queue = deque(queue.queue)
            if queue.current:
                queue.original_queue.appendleft(queue.current)
            await interaction.response.send_message("Looping queue", ephemeral=True)
        else:
            queue.loop_mode = 'off'
            queue.original_queue.clear()
            await interaction.response.send_message("Loop disabled", ephemeral=True)
        await update_now_playing(self.guild_id)

    @discord.ui.button(label="Shuffle", style=discord.ButtonStyle.secondary, custom_id="shuffle")
    async def shuffle(self, interaction: discord.Interaction, button: discord.ui.Button):
        queue = get_queue(self.guild_id)
        queue.shuffle_enabled = not queue.shuffle_enabled
        status = "ON" if queue.shuffle_enabled else "OFF"
        await interaction.response.send_message(f"Shuffle: {status}", ephemeral=True)
        await update_now_playing(self.guild_id)

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, custom_id="stop_button")
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await dj_check(interaction):
            return
            
        queue = get_queue(self.guild_id)
        queue.clear()
        queue.now_playing_message = None
        if interaction.guild.voice_client:
            interaction.guild.voice_client.stop()
        await interaction.response.send_message("Stopped!", ephemeral=True)
        # Stop the view from listening for more interactions
        super().stop()


class SearchSelectMenu(discord.ui.Select):
    def __init__(self, results, user_id):
        self.results = results
        self.user_id = user_id
        
        options = []
        for i, result in enumerate(results[:10], 1):
            duration = time.strftime('%M:%S', time.gmtime(result.get('duration', 0))) if result.get('duration') else "Live"
            title = result.get('title', 'Unknown')[:80]
            options.append(discord.SelectOption(
                label=f"{i}. {title[:50]}",
                description=f"{duration} - {result.get('uploader', 'Unknown')[:30]}",
                value=str(i-1)
            ))
        
        super().__init__(placeholder="Select a song to play...", options=options, custom_id="search_select")

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This search isn't yours!", ephemeral=True)
            return
        
        selected = self.results[int(self.values[0])]
        
        if not interaction.guild.voice_client:
            if interaction.user.voice:
                await interaction.user.voice.channel.connect()
            else:
                await interaction.response.send_message("Join a voice channel first!", ephemeral=True)
                return
        
        await interaction.response.defer()
        
        try:
            queue = get_queue(interaction.guild_id)
            queue.text_channel = interaction.channel
            player = await YTDLSource.from_url(
                selected['url'],
                loop=bot.loop,
                stream=True,
                requester=interaction.user,
                audio_filter=queue.audio_filter
            )
            player.volume = queue.volume / 100
            
            if interaction.guild.voice_client.is_playing() or interaction.guild.voice_client.is_paused():
                queue.add(player)
                embed = create_music_embed(player, "Added to Queue", queue)
                embed.add_field(name="Position", value=f"`#{len(queue.queue)}`", inline=True)
                await interaction.followup.send(embed=embed)
            else:
                queue.current = player
                interaction.guild.voice_client.play(
                    player,
                    after=lambda e: asyncio.run_coroutine_threadsafe(play_next(interaction.guild_id), bot.loop)
                )
                bot_stats['songs_played'] += 1
                embed = create_music_embed(player, "Now Playing", queue)
                msg = await interaction.followup.send(embed=embed, view=MusicControlView(interaction.guild_id))
                queue.now_playing_message = msg
        except Exception as e:
            await interaction.followup.send(f"Error: {str(e)}")


class SearchView(discord.ui.View):
    def __init__(self, results, user_id):
        super().__init__(timeout=120)
        self.add_item(SearchSelectMenu(results, user_id))

# ═══════════════════════════════════════════════════════════════════════════════
#                              BOT EVENTS
# ═══════════════════════════════════════════════════════════════════════════════

@bot.event
async def on_ready():
    print(f'''
    ╔════════════════════════════════════════════════════════╗
    ║           CORD TITAN V3 - ULTIMATE MUSIC BOT           ║
    ║                     NOW ONLINE!                        ║
    ╠════════════════════════════════════════════════════════╣
    ║  Bot: {bot.user.name:<46} ║
    ║  Servers: {len(bot.guilds):<42} ║
    ║  Users: {len(set(bot.get_all_members())):<44} ║
    ║  Latency: {round(bot.latency * 1000)}ms{' ' * 40}║
    ╚════════════════════════════════════════════════════════╝
    ''')
    
    bot_stats['servers'] = len(bot.guilds)
    change_status.start()
    check_voice_activity.start()
    
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

@bot.event
async def on_command(ctx):
    bot_stats['commands_used'] += 1

@bot.event
async def on_voice_state_update(member, before, after):
    if member.id == bot.user.id:
        if before.channel and not after.channel:
            guild_id = before.channel.guild.id
            if mode_247.get(guild_id, False):
                try:
                    await asyncio.sleep(2)
                    await before.channel.connect()
                except:
                    pass

# ═══════════════════════════════════════════════════════════════════════════════
#                              STATUS ROTATION
# ═══════════════════════════════════════════════════════════════════════════════

status_list = [
    ("/help for commands", discord.ActivityType.listening, discord.Status.online),
    ("in {} servers", discord.ActivityType.playing, discord.Status.dnd),
    ("{} songs played", discord.ActivityType.listening, discord.Status.idle),
    ("Cord Titan V3", discord.ActivityType.competing, discord.Status.online),
    ("Ultimate Music Bot", discord.ActivityType.watching, discord.Status.online),
]

@tasks.loop(minutes=2)
async def change_status():
    status_data = random.choice(status_list)
    status_text, activity_type, status = status_data
    
    if '{}' in status_text:
        if 'servers' in status_text:
            status_text = status_text.format(len(bot.guilds))
        elif 'songs' in status_text:
            status_text = status_text.format(bot_stats['songs_played'])
    
    activity = discord.Activity(type=activity_type, name=status_text)
    await bot.change_presence(status=status, activity=activity)

@tasks.loop(minutes=5)
async def check_voice_activity():
    for vc in bot.voice_clients:
        if mode_247.get(vc.guild.id, False):
            continue
        if len(vc.channel.members) == 1:
            queue = get_queue(vc.guild.id)
            queue.clear()
            await vc.disconnect()

# ═══════════════════════════════════════════════════════════════════════════════
#                              PLAY NEXT HANDLER
# ═══════════════════════════════════════════════════════════════════════════════

async def play_next(guild_id):
    """Handle playing the next song in queue"""
    queue = get_queue(guild_id)
    guild = bot.get_guild(guild_id)
    
    if not guild or not guild.voice_client:
        return
    
    if queue.loop_mode == 'song' and queue.current:
        try:
            new_source = await queue.current.recreate()
            new_source.requester = queue.current.requester
            new_source.volume = queue.volume / 100
            guild.voice_client.play(
                new_source,
                after=lambda e: asyncio.run_coroutine_threadsafe(play_next(guild_id), bot.loop)
            )
            bot_stats['songs_played'] += 1
            return
        except Exception as e:
            logger.error(f"Error in song loop: {e}")
    
    next_song = queue.next()
    
    if next_song:
        try:
            new_source = await next_song.recreate()
            new_source.requester = next_song.requester
            new_source.volume = queue.volume / 100
            guild.voice_client.play(
                new_source,
                after=lambda e: asyncio.run_coroutine_threadsafe(play_next(guild_id), bot.loop)
            )
            bot_stats['songs_played'] += 1
            
            if queue.text_channel:
                embed = create_music_embed(next_song, "Now Playing", queue)
                msg = await queue.text_channel.send(embed=embed, view=MusicControlView(guild_id))
                queue.now_playing_message = msg
        except Exception as e:
            logger.error(f"Error playing next: {e}")
            if queue.text_channel:
                await queue.text_channel.send(f"Error playing next song: {str(e)}")

# ═══════════════════════════════════════════════════════════════════════════════
#                              SLASH COMMANDS - PLAYBACK
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
#                    ADVANCED INTERACTION HANDLING & UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════

class InteractionHandler:
    """Advanced interaction handler with timeout protection and error recovery"""
    
    @staticmethod
    async def safe_defer(interaction: discord.Interaction, ephemeral: bool = False):
        """Safely defer an interaction with timeout protection"""
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=ephemeral)
                return True
        except discord.NotFound:
            logger.warning(f"Interaction expired before defer: {interaction.id}")
            return False
        except Exception as e:
            logger.error(f"Error deferring interaction: {e}")
            return False
        return True
    
    @staticmethod
    async def safe_response(interaction: discord.Interaction, embed=None, content=None, 
                          view=None, ephemeral=False, edit=False):
        """Safely send or edit interaction response with fallback handling"""
        try:
            if interaction.response.is_done():
                if edit and interaction.message:
                    return await interaction.message.edit(embed=embed, content=content, view=view)
                else:
                    return await interaction.followup.send(
                        embed=embed, content=content, view=view, ephemeral=ephemeral
                    )
            else:
                return await interaction.response.send_message(
                    embed=embed, content=content, view=view, ephemeral=ephemeral
                )
        except discord.NotFound:
            logger.warning(f"Interaction not found when responding: {interaction.id}")
            # Try channel fallback
            if interaction.channel:
                try:
                    return await interaction.channel.send(embed=embed, content=content, view=view)
                except:
                    pass
        except discord.HTTPException as e:
            logger.error(f"HTTP error in safe_response: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in safe_response: {e}")
        return None

class ProgressTracker:
    """Track and display progress for long-running operations"""
    
    def __init__(self, interaction: discord.Interaction, title: str, total_steps: int = 100):
        self.interaction = interaction
        self.title = title
        self.total_steps = total_steps
        self.current_step = 0
        self.message = None
        self.last_update = 0
        self.update_interval = 2  # Update every 2 seconds minimum
    
    async def start(self):
        """Initialize progress tracking"""
        embed = create_embed(self.title, "Starting...", 0x9b59b6)
        self.message = await InteractionHandler.safe_response(self.interaction, embed=embed)
        self.last_update = time.time()
    
    async def update(self, step: int, description: str = None):
        """Update progress with throttling"""
        self.current_step = step
        current_time = time.time()
        
        # Throttle updates to avoid rate limits
        if current_time - self.last_update < self.update_interval and step < self.total_steps:
            return
        
        percentage = int((step / self.total_steps) * 100)
        filled = int(percentage / 10)
        bar = "█" * filled + "░" * (10 - filled)
        
        embed = create_embed(
            self.title,
            f"{description or 'Processing...'}\n\n`{bar}` {percentage}%",
            0x9b59b6
        )
        
        if self.message:
            try:
                await self.message.edit(embed=embed)
                self.last_update = current_time
            except:
                pass
    
    async def complete(self, final_embed):
        """Mark operation as complete"""
        if self.message:
            try:
                await self.message.edit(embed=final_embed)
            except:
                pass

# ═══════════════════════════════════════════════════════════════════════════════
#                    ADVANCED PLAY COMMAND WITH SMART LOADING
# ═══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="play", description="Play a song from YouTube")
@app_commands.describe(query="Song name or YouTube URL")
async def play_slash(interaction: discord.Interaction, query: str):
    # Voice channel validation
    if not interaction.user.voice:
        embed = create_embed("❌ Error", "You need to be in a voice channel!", 0xf44336)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    # CRITICAL: Defer immediately (within 3 seconds)
    if not await InteractionHandler.safe_defer(interaction):
        return
    
    # Show initial loading message
    loading_embed = create_embed(
        "🔍 Searching",
        f"Looking for: **{query[:100]}**\n\nPlease wait...",
        0x9b59b6
    )
    loading_msg = await InteractionHandler.safe_response(interaction, embed=loading_embed)
    
    try:
        # Connect to voice if not already
        if not interaction.guild.voice_client:
            try:
                await interaction.user.voice.channel.connect()
            except Exception as e:
                embed = create_embed("❌ Connection Failed", f"Couldn't join voice: {str(e)}", 0xf44336)
                await InteractionHandler.safe_response(interaction, embed=embed, edit=True)
                return
        
        # Prepare search query
        if not query.startswith('http'):
            query = f'ytsearch:{query}'
        
        queue = get_queue(interaction.guild_id)
        queue.text_channel = interaction.channel
        
        # Update loading message
        if loading_msg:
            try:
                loading_embed.description = f"Looking for: **{query[:100]}**\n\n⏳ Fetching audio data..."
                await loading_msg.edit(embed=loading_embed)
            except:
                pass
        
        # Fetch audio source (this is the slow part)
        start_time = time.time()
        try:
            player = await asyncio.wait_for(
                YTDLSource.from_url(
                    query,
                    loop=bot.loop,
                    stream=True,
                    requester=interaction.user,
                    audio_filter=queue.audio_filter
                ),
                timeout=60  # 60 second timeout for fetching
            )
        except asyncio.TimeoutError:
            embed = create_embed(
                "⏱️ Timeout",
                "Request took too long. Try again or use a different song.",
                0xff9800
            )
            await InteractionHandler.safe_response(interaction, embed=embed, edit=True)
            return
        except Exception as e:
            embed = create_embed(
                "❌ Failed to Load",
                f"Error: {str(e)[:200]}\n\nTry a different song or check the URL.",
                0xf44336
            )
            await InteractionHandler.safe_response(interaction, embed=embed, edit=True)
            return
        
        fetch_time = time.time() - start_time
        logger.info(f"Fetched {player.title} in {fetch_time:.2f}s")
        
        player.volume = queue.volume / 100
        
        # Check if something is already playing
        if interaction.guild.voice_client.is_playing() or interaction.guild.voice_client.is_paused():
            queue.add(player)
            embed = create_music_embed(player, "✅ Added to Queue", queue)
            embed.add_field(name="Position", value=f"`#{len(queue.queue)}`", inline=True)
            embed.add_field(name="Queue Duration", value=f"`{format_queue_duration(queue.total_duration())}`", inline=True)
            embed.set_footer(text=f"Loaded in {fetch_time:.1f}s | Cord Titan V3")
            await InteractionHandler.safe_response(interaction, embed=embed, edit=True)
        else:
            # Start playing immediately
            queue.current = player
            try:
                interaction.guild.voice_client.play(
                    player,
                    after=lambda e: asyncio.run_coroutine_threadsafe(
                        play_next(interaction.guild_id), bot.loop
                    )
                )
                bot_stats['songs_played'] += 1
                
                embed = create_music_embed(player, "🎵 Now Playing", queue)
                embed.set_footer(text=f"Loaded in {fetch_time:.1f}s | Cord Titan V3")
                msg = await InteractionHandler.safe_response(
                    interaction, embed=embed, view=MusicControlView(interaction.guild_id), edit=True
                )
                queue.now_playing_message = msg
            except Exception as e:
                embed = create_embed("❌ Playback Error", f"Failed to play: {str(e)}", 0xf44336)
                await InteractionHandler.safe_response(interaction, embed=embed, edit=True)
                
    except Exception as e:
        logger.error(f"Unexpected error in play command: {e}", exc_info=True)
        embed = create_embed(
            "❌ Unexpected Error",
            f"Something went wrong: {str(e)[:200]}",
            0xf44336
        )
        await InteractionHandler.safe_response(interaction, embed=embed, edit=True)

# ═══════════════════════════════════════════════════════════════════════════════
#                    ADVANCED PLAYNEXT COMMAND
# ═══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="playnext", description="Add a song to play next")
@app_commands.describe(query="Song name or YouTube URL")
async def playnext_slash(interaction: discord.Interaction, query: str):
    if not interaction.user.voice:
        embed = create_embed("❌ Error", "You need to be in a voice channel!", 0xf44336)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    if not await InteractionHandler.safe_defer(interaction):
        return
    
    loading_embed = create_embed("🔍 Searching", f"Looking for: **{query[:100]}**", 0x9b59b6)
    loading_msg = await InteractionHandler.safe_response(interaction, embed=loading_embed)
    
    try:
        if not interaction.guild.voice_client:
            await interaction.user.voice.channel.connect()
        
        if not query.startswith('http'):
            query = f'ytsearch:{query}'
        
        queue = get_queue(interaction.guild_id)
        queue.text_channel = interaction.channel
        
        start_time = time.time()
        player = await asyncio.wait_for(
            YTDLSource.from_url(
                query, loop=bot.loop, stream=True,
                requester=interaction.user, audio_filter=queue.audio_filter
            ),
            timeout=60
        )
        fetch_time = time.time() - start_time
        
        player.volume = queue.volume / 100
        queue.add_next(player)
        
        embed = create_music_embed(player, "⏭️ Added Next", queue)
        embed.add_field(name="Position", value="`#1 (Next)`", inline=True)
        embed.set_footer(text=f"Loaded in {fetch_time:.1f}s | Cord Titan V3")
        await InteractionHandler.safe_response(interaction, embed=embed, edit=True)
        
    except asyncio.TimeoutError:
        embed = create_embed("⏱️ Timeout", "Request took too long. Try again.", 0xff9800)
        await InteractionHandler.safe_response(interaction, embed=embed, edit=True)
    except Exception as e:
        logger.error(f"Error in playnext: {e}")
        embed = create_embed("❌ Error", f"Failed to add: {str(e)[:200]}", 0xf44336)
        await InteractionHandler.safe_response(interaction, embed=embed, edit=True)

# ═══════════════════════════════════════════════════════════════════════════════
#                    ADVANCED SEARCH COMMAND
# ═══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="search", description="Search for songs on YouTube")
@app_commands.describe(query="Search query")
async def search_slash(interaction: discord.Interaction, query: str):
    if not await InteractionHandler.safe_defer(interaction):
        return
    
    loading_embed = create_embed("🔍 Searching YouTube", f"Query: **{query}**\n\nSearching...", 0x9b59b6)
    loading_msg = await InteractionHandler.safe_response(interaction, embed=loading_embed)
    
    try:
        search_query = f'ytsearch10:{query}'
        
        # Fetch search results with timeout
        start_time = time.time()
        data = await asyncio.wait_for(
            bot.loop.run_in_executor(
                None, lambda: ytdl.extract_info(search_query, download=False)
            ),
            timeout=30
        )
        search_time = time.time() - start_time
        
        if 'entries' not in data or not data['entries']:
            embed = create_embed("❌ No Results", "No videos found. Try different keywords.", 0xf44336)
            await InteractionHandler.safe_response(interaction, embed=embed, edit=True)
            return
        
        results = []
        embed = discord.Embed(
            title="🔍 Search Results",
            description=f"**Query:** `{query}`\n\nSelect a song from the dropdown below:",
            color=0x9b59b6
        )
        
        for i, entry in enumerate(data['entries'][:10], 1):
            if not entry:
                continue
            
            duration = time.strftime('%M:%S', time.gmtime(entry.get('duration', 0))) if entry.get('duration') else "🔴 Live"
            title = entry.get('title', 'Unknown')
            uploader = entry.get('uploader', 'Unknown')
            views = entry.get('view_count', 0)
            
            embed.add_field(
                name=f"`{i}.` {title[:55]}{'...' if len(title) > 55 else ''}",
                value=f"⏱️ `{duration}` | 👤 `{uploader[:20]}` | 👁️ `{views:,}`",
                inline=False
            )
            
            results.append({
                'title': title,
                'url': entry.get('webpage_url'),
                'duration': entry.get('duration'),
                'thumbnail': entry.get('thumbnail'),
                'uploader': uploader,
                'views': views
            })
        
        embed.set_footer(text=f"Found {len(results)} results in {search_time:.1f}s | Expires in 2 minutes")
        await InteractionHandler.safe_response(
            interaction, embed=embed, view=SearchView(results, interaction.user.id), edit=True
        )
        
    except asyncio.TimeoutError:
        embed = create_embed("⏱️ Timeout", "Search took too long. Try again.", 0xff9800)
        await InteractionHandler.safe_response(interaction, embed=embed, edit=True)
    except Exception as e:
        logger.error(f"Search error: {e}")
        embed = create_embed("❌ Search Failed", f"Error: {str(e)[:200]}", 0xf44336)
        await InteractionHandler.safe_response(interaction, embed=embed, edit=True)

# ═══════════════════════════════════════════════════════════════════════════════
#                    ADVANCED PLAYLIST COMMAND WITH PROGRESS
# ═══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="playlist", description="Play a YouTube playlist")
@app_commands.describe(url="YouTube playlist URL")
async def playlist_slash(interaction: discord.Interaction, url: str):
    if not interaction.user.voice:
        embed = create_embed("❌ Error", "Join a voice channel first!", 0xf44336)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    if not await InteractionHandler.safe_defer(interaction):
        return
    
    try:
        if not interaction.guild.voice_client:
            await interaction.user.voice.channel.connect()
        
        # Initialize progress tracker
        progress = ProgressTracker(interaction, "📥 Loading Playlist", 100)
        await progress.start()
        
        # Fetch playlist data
        await progress.update(10, "Fetching playlist information...")
        data = await asyncio.wait_for(
            bot.loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=False)),
            timeout=60
        )
        
        if 'entries' not in data:
            embed = create_embed("❌ Error", "Invalid playlist URL!", 0xf44336)
            await InteractionHandler.safe_response(interaction, embed=embed, edit=True)
            return
        
        queue = get_queue(interaction.guild_id)
        queue.text_channel = interaction.channel
        
        entries = [e for e in data['entries'][:100] if e]
        total_songs = len(entries)
        added_count = 0
        failed_count = 0
        first_song = None
        
        await progress.update(20, f"Found {total_songs} songs. Loading...")
        
        # Process songs with progress updates
        for idx, entry in enumerate(entries):
            try:
                player = await asyncio.wait_for(
                    YTDLSource.from_url(
                        entry['webpage_url'], loop=bot.loop, stream=True,
                        requester=interaction.user, audio_filter=queue.audio_filter
                    ),
                    timeout=30
                )
                player.volume = queue.volume / 100
                
                if not first_song and not interaction.guild.voice_client.is_playing():
                    first_song = player
                    queue.current = player
                    interaction.guild.voice_client.play(
                        player,
                        after=lambda e: asyncio.run_coroutine_threadsafe(
                            play_next(interaction.guild_id), bot.loop
                        )
                    )
                    bot_stats['songs_played'] += 1
                else:
                    queue.add(player)
                
                added_count += 1
                
                # Update progress
                progress_pct = 20 + int((idx / total_songs) * 80)
                await progress.update(
                    progress_pct,
                    f"Added {added_count}/{total_songs} songs\nCurrent: {entry.get('title', 'Unknown')[:40]}..."
                )
                
            except asyncio.TimeoutError:
                failed_count += 1
                logger.warning(f"Timeout loading playlist song: {entry.get('title', 'Unknown')}")
            except Exception as e:
                failed_count += 1
                logger.warning(f"Error loading playlist song: {e}")
                continue
        
        # Final result
        final_embed = create_embed(
            "✅ Playlist Added",
            f"**{data.get('title', 'Playlist')}**\n\n"
            f"✅ Added: **{added_count}** songs\n"
            f"{'❌ Failed: **' + str(failed_count) + '** songs' if failed_count > 0 else ''}\n"
            f"⏱️ Total Duration: `{format_queue_duration(queue.total_duration())}`",
            0x4caf50
        )
        
        if first_song and first_song.thumbnail:
            final_embed.set_thumbnail(url=first_song.thumbnail)
        
        await progress.complete(final_embed)
        
    except asyncio.TimeoutError:
        embed = create_embed("⏱️ Timeout", "Playlist loading took too long.", 0xff9800)
        await InteractionHandler.safe_response(interaction, embed=embed, edit=True)
    except Exception as e:
        logger.error(f"Playlist error: {e}", exc_info=True)
        embed = create_embed("❌ Error", f"Failed to load playlist: {str(e)[:200]}", 0xf44336)
        await InteractionHandler.safe_response(interaction, embed=embed, edit=True)
# ═══════════════════════════════════════════════════════════════════════════════
#                              SLASH COMMANDS - QUEUE
# ═══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="queue", description="Show the music queue")
@app_commands.describe(page="Page number")
async def queue_slash(interaction: discord.Interaction, page: int = 1):
    queue = get_queue(interaction.guild_id)
    
    if queue.current is None and queue.is_empty():
        embed = create_embed("Empty Queue", "No songs! Use `/play` to add music.", 0xff9800)
        await interaction.response.send_message(embed=embed)
        return
    
    items_per_page = 10
    pages = max(1, (len(queue.queue) + items_per_page - 1) // items_per_page)
    page = max(1, min(page, pages))
    start = (page - 1) * items_per_page
    end = start + items_per_page
    
    embed = discord.Embed(title="Music Queue", color=0x9b59b6, timestamp=datetime.utcnow())
    
    if queue.current:
        duration = queue.current.format_duration()
        embed.add_field(
            name="Now Playing",
            value=f"**{queue.current.title}**\n`{duration}` - {queue.current.requester.mention if queue.current.requester else 'Unknown'}",
            inline=False
        )
    
    if not queue.is_empty():
        queue_text = ""
        for i, song in enumerate(list(queue.queue)[start:end], start + 1):
            duration = song.format_duration()
            queue_text += f"`{i}.` **{song.title[:45]}{'...' if len(song.title) > 45 else ''}**\n"
            queue_text += f"    `{duration}` - {song.requester.mention if song.requester else 'Unknown'}\n"
        embed.add_field(name="Up Next", value=queue_text or "Empty", inline=False)
    
    total_duration = format_queue_duration(queue.total_duration())
    loop_icons = {'off': 'Off', 'song': 'Song', 'queue': 'Queue'}
    
    stats = f"Loop: `{loop_icons[queue.loop_mode]}` | "
    stats += f"Shuffle: `{'ON' if queue.shuffle_enabled else 'OFF'}` | "
    stats += f"Volume: `{queue.volume}%`\n"
    stats += f"Songs: `{len(queue.queue)}` | Duration: `{total_duration}`"
    
    if queue.audio_filter != 'normal':
        stats += f" | Filter: `{queue.audio_filter.upper()}`"
    
    embed.add_field(name="Settings", value=stats, inline=False)
    embed.set_footer(text=f"Cord Titan V3 | Page {page}/{pages}")
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="nowplaying", description="Show current song with controls")
async def nowplaying_slash(interaction: discord.Interaction):
    queue = get_queue(interaction.guild_id)
    
    if queue.current:
        embed = create_music_embed(queue.current, "Now Playing", queue)
        msg = await interaction.response.send_message(embed=embed, view=MusicControlView(interaction.guild_id))
        queue.now_playing_message = await interaction.original_response()
    else:
        embed = create_embed("Nothing Playing", "No song playing!", 0xf44336)
        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="clear", description="Clear the entire queue")
async def clear_slash(interaction: discord.Interaction):
    if not await dj_check(interaction):
        return
        
    queue = get_queue(interaction.guild_id)
    cleared = len(queue.queue)
    queue.queue.clear()
    queue.original_queue.clear()
    embed = create_embed("Queue Cleared", f"Removed **{cleared}** songs!", 0x4caf50)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="remove", description="Remove a song from queue")
@app_commands.describe(position="Song position (1-based)")
async def remove_slash(interaction: discord.Interaction, position: int):
    queue = get_queue(interaction.guild_id)
    removed = queue.remove(position - 1)
    
    if removed:
        embed = create_embed("Removed", f"Removed **{removed.title}** from position #{position}", 0x4caf50)
        await interaction.response.send_message(embed=embed)
    else:
        embed = create_embed("Invalid Position", f"Position #{position} doesn't exist!", 0xf44336)
        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="move", description="Move a song in the queue")
@app_commands.describe(from_pos="Current position", to_pos="New position")
async def move_slash(interaction: discord.Interaction, from_pos: int, to_pos: int):
    queue = get_queue(interaction.guild_id)
    moved = queue.move(from_pos - 1, to_pos - 1)
    
    if moved:
        embed = create_embed("Moved", f"Moved **{moved.title}** from #{from_pos} to #{to_pos}", 0x4caf50)
        await interaction.response.send_message(embed=embed)
    else:
        embed = create_embed("Invalid Position", "Invalid positions!", 0xf44336)
        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="skipto", description="Skip to a specific song in queue")
@app_commands.describe(position="Song position to skip to")
async def skipto_slash(interaction: discord.Interaction, position: int):
    if not await dj_check(interaction):
        return
        
    queue = get_queue(interaction.guild_id)
    
    if position < 1 or position > len(queue.queue):
        embed = create_embed("Invalid Position", f"Position must be 1-{len(queue.queue)}!", 0xf44336)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    target = queue.skipto(position - 1)
    
    if target and interaction.guild.voice_client:
        interaction.guild.voice_client.stop()
        embed = create_embed("Skipping To", f"Skipping to **{target.title}**", 0x4caf50)
        await interaction.response.send_message(embed=embed)
    else:
        embed = create_embed("Error", "Failed to skip!", 0xf44336)
        await interaction.response.send_message(embed=embed, ephemeral=True)

# ═══════════════════════════════════════════════════════════════════════════════
#                              SLASH COMMANDS - SETTINGS
# ═══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="volume", description="Change the volume (0-200%)")
@app_commands.describe(level="Volume level")
async def volume_slash(interaction: discord.Interaction, level: int):
    if not interaction.guild.voice_client:
        embed = create_embed("Not Connected", "Not in a voice channel!", 0xf44336)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    if not 0 <= level <= 200:
        embed = create_embed("Invalid", "Volume must be 0-200!", 0xf44336)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    queue = get_queue(interaction.guild_id)
    queue.volume = level
    
    if interaction.guild.voice_client.source:
        interaction.guild.voice_client.source.volume = level / 100
    
    filled = int(level / 200 * 10)
    bar = "█" * filled + "░" * (10 - filled)
    
    embed = create_embed("Volume Updated", f"Volume: **{level}%**\n`{bar}`", 0x4caf50)
    await interaction.response.send_message(embed=embed)
    await update_now_playing(interaction.guild_id)

@bot.tree.command(name="loop", description="Set loop mode")
@app_commands.describe(mode="Loop mode")
@app_commands.choices(mode=[
    app_commands.Choice(name="Off - Normal playback", value="off"),
    app_commands.Choice(name="Song - Repeat current song", value="song"),
    app_commands.Choice(name="Queue - Repeat entire queue", value="queue")
])
async def loop_slash(interaction: discord.Interaction, mode: app_commands.Choice[str]):
    queue = get_queue(interaction.guild_id)
    old_mode = queue.loop_mode
    queue.loop_mode = mode.value
    
    if mode.value == 'queue' and old_mode != 'queue':
        queue.original_queue = deque(queue.queue)
        if queue.current:
            queue.original_queue.appendleft(queue.current)
    
    if mode.value != 'queue' and old_mode == 'queue':
        queue.original_queue.clear()
    
    descriptions = {
        'off': 'Songs play normally without repeating',
        'song': 'Current song repeats indefinitely',
        'queue': 'Queue restarts when finished'
    }
    
    embed = create_embed(f"Loop: {mode.value.capitalize()}", descriptions[mode.value], 0x9b59b6)
    await interaction.response.send_message(embed=embed)
    await update_now_playing(interaction.guild_id)

@bot.tree.command(name="shuffle", description="Toggle shuffle mode")
async def shuffle_slash(interaction: discord.Interaction):
    queue = get_queue(interaction.guild_id)
    queue.shuffle_enabled = not queue.shuffle_enabled
    
    status = "Enabled" if queue.shuffle_enabled else "Disabled"
    description = "Songs will play in random order" if queue.shuffle_enabled else "Songs play in queue order"
    
    embed = create_embed(f"Shuffle {status}", description, 0x9b59b6)
    await interaction.response.send_message(embed=embed)
    await update_now_playing(interaction.guild_id)

# ═══════════════════════════════════════════════════════════════════════════════
#                              SLASH COMMANDS - AUDIO FILTERS
# ═══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="filter", description="Apply audio filter/effect")
@app_commands.describe(effect="Choose an audio effect")
@app_commands.choices(effect=[
    app_commands.Choice(name="Normal - Default audio", value="normal"),
    app_commands.Choice(name="Bass Boost - Enhanced bass", value="bassboost"),
    app_commands.Choice(name="Super Bass - Maximum bass", value="superbass"),
    app_commands.Choice(name="Nightcore - Faster & higher pitch", value="nightcore"),
    app_commands.Choice(name="Vaporwave - Slower & lower pitch", value="vaporwave"),
    app_commands.Choice(name="Treble - Enhanced highs", value="treble"),
    app_commands.Choice(name="8D Audio - Surround effect", value="8d"),
    app_commands.Choice(name="Karaoke - Reduced vocals", value="karaoke"),
    app_commands.Choice(name="Soft - Quieter & mellow", value="soft"),
    app_commands.Choice(name="Loud - Boosted volume", value="loud")
])
async def filter_slash(interaction: discord.Interaction, effect: app_commands.Choice[str]):
    queue = get_queue(interaction.guild_id)
    queue.audio_filter = effect.value
    
    if queue.current and interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
        await interaction.response.defer()
        
        try:
            current_song = queue.current
            new_source = await current_song.recreate(audio_filter=effect.value)
            new_source.requester = current_song.requester
            new_source.volume = queue.volume / 100
            
            interaction.guild.voice_client.stop()
            
            interaction.guild.voice_client.play(
                new_source,
                after=lambda e: asyncio.run_coroutine_threadsafe(play_next(interaction.guild_id), bot.loop)
            )
            
            queue.current = new_source
            
            embed = create_embed(
                f"Filter Applied: {effect.name}",
                f"Now playing with **{effect.value.upper()}** effect!",
                0x9b59b6
            )
            await interaction.followup.send(embed=embed)
            await update_now_playing(interaction.guild_id)
        except Exception as e:
            embed = create_embed("Error", f"Failed to apply filter: {str(e)}", 0xf44336)
            await interaction.followup.send(embed=embed)
    else:
        embed = create_embed(
            f"Filter Set: {effect.name}",
            f"Next songs will play with **{effect.value.upper()}** effect!",
            0x9b59b6
        )
        await interaction.response.send_message(embed=embed)

@bot.tree.command(name="bassboost", description="Toggle bass boost on/off")
async def bassboost_slash(interaction: discord.Interaction):
    queue = get_queue(interaction.guild_id)
    
    if queue.audio_filter == 'bassboost':
        queue.audio_filter = 'normal'
        status = "Disabled"
    else:
        queue.audio_filter = 'bassboost'
        status = "Enabled"
    
    if queue.current and interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
        await interaction.response.defer()
        
        try:
            new_source = await queue.current.recreate(audio_filter=queue.audio_filter)
            new_source.requester = queue.current.requester
            new_source.volume = queue.volume / 100
            
            interaction.guild.voice_client.stop()
            
            interaction.guild.voice_client.play(
                new_source,
                after=lambda e: asyncio.run_coroutine_threadsafe(play_next(interaction.guild_id), bot.loop)
            )
            
            queue.current = new_source
            
            embed = create_embed(f"Bass Boost {status}", "Effect applied to current song!", 0x9b59b6)
            await interaction.followup.send(embed=embed)
            await update_now_playing(interaction.guild_id)
        except Exception as e:
            embed = create_embed("Error", f"Failed: {str(e)}", 0xf44336)
            await interaction.followup.send(embed=embed)
    else:
        embed = create_embed(f"Bass Boost {status}", "Will apply to next songs!", 0x9b59b6)
        await interaction.response.send_message(embed=embed)

@bot.tree.command(name="nightcore", description="Toggle nightcore effect on/off")
async def nightcore_slash(interaction: discord.Interaction):
    queue = get_queue(interaction.guild_id)
    
    if queue.audio_filter == 'nightcore':
        queue.audio_filter = 'normal'
        status = "Disabled"
    else:
        queue.audio_filter = 'nightcore'
        status = "Enabled"
    
    if queue.current and interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
        await interaction.response.defer()
        
        try:
            new_source = await queue.current.recreate(audio_filter=queue.audio_filter)
            new_source.requester = queue.current.requester
            new_source.volume = queue.volume / 100
            
            interaction.guild.voice_client.stop()
            
            interaction.guild.voice_client.play(
                new_source,
                after=lambda e: asyncio.run_coroutine_threadsafe(play_next(interaction.guild_id), bot.loop)
            )
            
            queue.current = new_source
            
            embed = create_embed(f"Nightcore {status}", "Effect applied!", 0x9b59b6)
            await interaction.followup.send(embed=embed)
            await update_now_playing(interaction.guild_id)
        except Exception as e:
            embed = create_embed("Error", f"Failed: {str(e)}", 0xf44336)
            await interaction.followup.send(embed=embed)
    else:
        embed = create_embed(f"Nightcore {status}", "Will apply to next songs!", 0x9b59b6)
        await interaction.response.send_message(embed=embed)

# ═══════════════════════════════════════════════════════════════════════════════
#                              SLASH COMMANDS - UTILITY
# ═══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="join", description="Join your voice channel")
async def join_slash(interaction: discord.Interaction):
    if not interaction.user.voice:
        embed = create_embed("Error", "You need to be in a voice channel!", 0xf44336)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    channel = interaction.user.voice.channel
    
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.move_to(channel)
        embed = create_embed("Moved", f"Moved to **{channel.name}**!", 0x4caf50)
    else:
        await channel.connect()
        embed = create_embed("Connected", f"Joined **{channel.name}**!", 0x4caf50)
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="leave", description="Leave the voice channel")
async def leave_slash(interaction: discord.Interaction):
    if interaction.guild.voice_client:
        queue = get_queue(interaction.guild_id)
        queue.clear()
        queue.now_playing_message = None
        await interaction.guild.voice_client.disconnect()
        embed = create_embed("Disconnected", "Left voice channel and cleared queue!", 0x4caf50)
        await interaction.response.send_message(embed=embed)
    else:
        embed = create_embed("Not Connected", "Not in a voice channel!", 0xf44336)
        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="247", description="Toggle 24/7 mode (never disconnect)")
async def mode247_slash(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        embed = create_embed("Admin Only", "This command requires admin permissions!", 0xf44336)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    guild_id = interaction.guild_id
    mode_247[guild_id] = not mode_247.get(guild_id, False)
    
    if mode_247[guild_id]:
        embed = create_embed("24/7 Mode Enabled", "Bot will stay in voice channel permanently!", 0x4caf50)
    else:
        embed = create_embed("24/7 Mode Disabled", "Bot will disconnect when alone in voice channel.", 0xff9800)
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="grab", description="DM yourself the current song")
async def grab_slash(interaction: discord.Interaction):
    queue = get_queue(interaction.guild_id)
    
    if not queue.current:
        embed = create_embed("Nothing Playing", "No song to grab!", 0xf44336)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    try:
        song = queue.current
        embed = discord.Embed(
            title="Saved Song",
            description=f"**{song.title}**",
            color=0x9b59b6,
            url=song.webpage_url
        )
        
        if song.thumbnail:
            embed.set_thumbnail(url=song.thumbnail)
        
        embed.add_field(name="Duration", value=f"`{song.format_duration()}`", inline=True)
        embed.add_field(name="Channel", value=f"`{song.uploader}`", inline=True)
        embed.add_field(name="Link", value=f"[Click Here]({song.webpage_url})", inline=False)
        embed.set_footer(text=f"Saved from {interaction.guild.name}")
        
        await interaction.user.send(embed=embed)
        
        confirm_embed = create_embed("Song Grabbed", "Check your DMs!", 0x4caf50)
        await interaction.response.send_message(embed=confirm_embed, ephemeral=True)
    except discord.Forbidden:
        embed = create_embed("Error", "Couldn't DM you! Enable DMs from server members.", 0xf44336)
        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="songinfo", description="Get detailed info about current song")
async def songinfo_slash(interaction: discord.Interaction):
    queue = get_queue(interaction.guild_id)
    
    if not queue.current:
        embed = create_embed("Nothing Playing", "No song playing!", 0xf44336)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    song = queue.current
    
    embed = discord.Embed(
        title="Song Information",
        description=f"**[{song.title}]({song.webpage_url})**",
        color=0x00bcd4
    )
    
    if song.thumbnail:
        embed.set_image(url=song.thumbnail)
    
    embed.add_field(name="Duration", value=f"`{song.format_duration()}`", inline=True)
    embed.add_field(name="Channel", value=f"`{song.uploader}`", inline=True)
    embed.add_field(name="Requested by", value=song.requester.mention if song.requester else "Unknown", inline=True)
    
    if song.views:
        embed.add_field(name="Views", value=f"`{song.views:,}`", inline=True)
    if song.likes:
        embed.add_field(name="Likes", value=f"`{song.likes:,}`", inline=True)
    
    if song.upload_date:
        try:
            date = datetime.strptime(song.upload_date, '%Y%m%d').strftime('%B %d, %Y')
            embed.add_field(name="Upload Date", value=f"`{date}`", inline=True)
        except:
            pass
    
    if song.audio_filter != 'normal':
        embed.add_field(name="Active Filter", value=f"`{song.audio_filter.upper()}`", inline=True)
    
    if song.description:
        desc = song.description[:300] + "..." if len(song.description) > 300 else song.description
        embed.add_field(name="Description", value=desc, inline=False)
    
    embed.set_footer(text="Cord Titan V3")
    await interaction.response.send_message(embed=embed)

# ═══════════════════════════════════════════════════════════════════════════════
#                              SLASH COMMANDS - DJ & ADMIN
# ═══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="setdj", description="Set DJ role (Admin only)")
@app_commands.describe(role="Role that can control music")
async def setdj_slash(interaction: discord.Interaction, role: discord.Role = None):
    if not interaction.user.guild_permissions.administrator:
        embed = create_embed("Admin Only", "This command requires admin permissions!", 0xf44336)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    if role:
        dj_roles[interaction.guild_id] = role.id
        embed = create_embed("DJ Role Set", f"DJ role set to {role.mention}\n\nOnly users with this role (or admins) can control music.", 0x4caf50)
    else:
        if interaction.guild_id in dj_roles:
            del dj_roles[interaction.guild_id]
        embed = create_embed("DJ Role Removed", "Anyone can now control music!", 0xff9800)
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="playlist", description="Play a YouTube playlist")
@app_commands.describe(url="YouTube playlist URL")
async def playlist_slash(interaction: discord.Interaction, url: str):
    if not interaction.user.voice:
        embed = create_embed("Error", "Join a voice channel first!", 0xf44336)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    await interaction.response.defer()
    
    if not interaction.guild.voice_client:
        await interaction.user.voice.channel.connect()
    
    try:
        data = await bot.loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=False))
        
        if 'entries' not in data:
            embed = create_embed("Error", "Invalid playlist URL!", 0xf44336)
            await interaction.followup.send(embed=embed)
            return
        
        queue = get_queue(interaction.guild_id)
        queue.text_channel = interaction.channel
        added_count = 0
        first_song = None
        
        embed = create_embed("Loading Playlist", f"**{data.get('title', 'Playlist')}**\n\nAdding songs...", 0x9b59b6)
        msg = await interaction.followup.send(embed=embed)
        
        for entry in data['entries'][:100]:
            if entry:
                try:
                    player = await YTDLSource.from_url(
                        entry['webpage_url'],
                        loop=bot.loop,
                        stream=True,
                        requester=interaction.user,
                        audio_filter=queue.audio_filter
                    )
                    player.volume = queue.volume / 100
                    
                    if not first_song and not interaction.guild.voice_client.is_playing():
                        first_song = player
                        queue.current = player
                        interaction.guild.voice_client.play(
                            player,
                            after=lambda e: asyncio.run_coroutine_threadsafe(play_next(interaction.guild_id), bot.loop)
                        )
                        bot_stats['songs_played'] += 1
                    else:
                        queue.add(player)
                    
                    added_count += 1
                except:
                    continue
        
        embed = create_embed(
            "Playlist Added",
            f"**{data.get('title', 'Playlist')}**\n\nAdded **{added_count}** songs to the queue!",
            0x4caf50
        )
        await msg.edit(embed=embed)
        
    except Exception as e:
        embed = create_embed("Error", f"Failed to load playlist: {str(e)}", 0xf44336)
        await interaction.followup.send(embed=embed)

# ═══════════════════════════════════════════════════════════════════════════════
#                              SLASH COMMANDS - STATS & HELP
# ═══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="stats", description="Show bot statistics")
async def stats_slash(interaction: discord.Interaction):
    uptime = time.time() - bot_stats['uptime_start']
    days, remainder = divmod(int(uptime), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    if days > 0:
        uptime_str = f"{days}d {hours}h {minutes}m"
    else:
        uptime_str = f"{hours}h {minutes}m {seconds}s"
    
    embed = discord.Embed(
        title="Cord Titan V3 Statistics",
        description="Ultimate Music Bot Stats",
        color=0x9b59b6,
        timestamp=datetime.utcnow()
    )
    
    embed.add_field(name="Uptime", value=f"`{uptime_str}`", inline=True)
    embed.add_field(name="Songs Played", value=f"`{bot_stats['songs_played']:,}`", inline=True)
    embed.add_field(name="Commands Used", value=f"`{bot_stats['commands_used']:,}`", inline=True)
    embed.add_field(name="Servers", value=f"`{len(bot.guilds):,}`", inline=True)
    embed.add_field(name="Users", value=f"`{len(set(bot.get_all_members())):,}`", inline=True)
    embed.add_field(name="Voice Connections", value=f"`{len(bot.voice_clients)}`", inline=True)
    embed.add_field(name="Latency", value=f"`{round(bot.latency * 1000)}ms`", inline=True)
    
    try:
        import psutil
        process = psutil.Process()
        memory_mb = process.memory_info().rss / 1024 / 1024
        embed.add_field(name="Memory", value=f"`{memory_mb:.1f} MB`", inline=True)
    except:
        pass
    
    embed.set_footer(text="Cord Titan V3")
    embed.set_thumbnail(url="https://i.imghippo.com/files/KVj9306xLE.png")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="help", description="Show all commands")
async def help_slash(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Cord Titan V3 - Command Guide",
        description="**The Ultimate Discord Music Bot!**\n\nUse `/play [song]` to start playing music!",
        color=0x9b59b6,
        timestamp=datetime.utcnow()
    )
    
    playback = (
        "`/play` - Play a song\n"
        "`/playnext` - Add song to play next\n"
        "`/search` - Search YouTube\n"
        "`/pause` - Pause playback\n"
        "`/resume` - Resume playback\n"
        "`/skip` - Skip current song\n"
        "`/voteskip` - Vote to skip\n"
        "`/previous` - Play previous song\n"
        "`/replay` - Restart current song\n"
        "`/stop` - Stop & clear queue"
    )
    
    queue_cmds = (
        "`/queue` - View queue\n"
        "`/nowplaying` - Current song info\n"
        "`/clear` - Clear queue\n"
        "`/remove` - Remove song\n"
        "`/move` - Move song position\n"
        "`/skipto` - Skip to position\n"
        "`/playlist` - Play YouTube playlist"
    )
    
    settings = (
        "`/volume` - Adjust volume (0-200%)\n"
        "`/loop` - Loop mode (off/song/queue)\n"
        "`/shuffle` - Toggle shuffle"
    )
    
    filters = (
        "`/filter` - Apply audio effect\n"
        "`/bassboost` - Toggle bass boost\n"
        "`/nightcore` - Toggle nightcore\n"
        "*Effects: Normal, Bass, Super Bass,*\n"
        "*Nightcore, Vaporwave, Treble, 8D,*\n"
        "*Karaoke, Soft, Loud*"
    )
    
    utility = (
        "`/join` - Join voice channel\n"
        "`/leave` - Leave voice channel\n"
        "`/247` - Toggle 24/7 mode\n"
        "`/grab` - DM current song\n"
        "`/songinfo` - Detailed song info\n"
        "`/setdj` - Set DJ role (Admin)\n"
        "`/stats` - Bot statistics"
    )
    
    embed.add_field(name="Playback", value=playback, inline=True)
    embed.add_field(name="Queue", value=queue_cmds, inline=True)
    embed.add_field(name="Settings", value=settings, inline=True)
    embed.add_field(name="Audio Filters", value=filters, inline=True)
    embed.add_field(name="Utility", value=utility, inline=True)
    
    embed.add_field(
        name="Pro Tips",
        value=(
            "- Use the buttons on Now Playing for quick controls\n"
            "- Select from dropdown after `/search`\n"
            "- Try `/filter 8d` for immersive audio\n"
            "- Volume can go up to 200% for quiet songs\n"
            "- Use `/247` to keep the bot always connected"
        ),
        inline=False
    )
    
    embed.set_footer(text="Cord Titan V3")
    embed.set_thumbnail(url="https://i.imghippo.com/files/KVj9306xLE.png")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="ping", description="Check bot latency")
async def ping_slash(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    
    if latency < 100:
        status = "Excellent"
        color = 0x4caf50
    elif latency < 200:
        status = "Good"
        color = 0xff9800
    else:
        status = "High"
        color = 0xf44336
    
    embed = discord.Embed(
        title="Pong!",
        description=f"**Latency:** `{latency}ms`\n**Status:** {status}",
        color=color
    )
    await interaction.response.send_message(embed=embed)

# ═══════════════════════════════════════════════════════════════════════════════
#                              ERROR HANDLING
# ═══════════════════════════════════════════════════════════════════════════════

@bot.event
async def on_error(event, *args, **kwargs):
    logger.error(f'Error in {event}: {args} {kwargs}')

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandOnCooldown):
        embed = create_embed("Cooldown", f"Wait {error.retry_after:.1f}s", 0xff9800)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    elif isinstance(error, app_commands.MissingPermissions):
        embed = create_embed("Missing Permissions", "You don't have permission!", 0xf44336)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        logger.error(f"Command error: {error}")
        embed = create_embed("Error", f"An error occurred: {str(error)[:200]}", 0xf44336)
        try:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except:
            await interaction.followup.send(embed=embed, ephemeral=True)

# ═══════════════════════════════════════════════════════════════════════════════
#                              MAIN EXECUTION
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    
    TOKEN = os.getenv('DISCORD_BOT_TOKEN')
    
    if not TOKEN:
        print("ERROR: DISCORD_BOT_TOKEN not found!")
        print("Create a .env file with: DISCORD_BOT_TOKEN=your_token_here")
        exit(1)
    
    print("Starting Cord Titan V3 - Ultimate Music Bot...")
    
    try:
        bot.run(TOKEN)
    except discord.LoginFailure:
        print("Invalid bot token! Check your DISCORD_BOT_TOKEN")
    except Exception as e:
        print(f"Failed to start bot: {e}")
