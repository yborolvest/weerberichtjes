# Discord Posting Setup Guide

This guide will help you set up automatic posting of weather videos to Discord twice daily.

## Step 1: Create a Discord Webhook

1. Open Discord and go to your server
2. Go to **Server Settings** → **Integrations** → **Webhooks**
3. Click **New Webhook**
4. Give it a name (e.g., "Weather Bot")
5. Choose the channel where you want videos posted
6. Click **Copy Webhook URL**
7. Save this URL - you'll need it in the next step

## Step 2: Set the Webhook URL

You have two options:

### Option A: Environment Variable (Recommended)

Add this to your shell profile (`~/.zshrc` or `~/.bash_profile`):

```bash
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/YOUR_WEBHOOK_ID/YOUR_WEBHOOK_TOKEN"
```

Then reload your shell:
```bash
source ~/.zshrc
```

### Option B: Pass as Argument (for testing)

You can modify the script to pass the webhook URL directly, or set it as an environment variable when running:

```bash
DISCORD_WEBHOOK_URL="your_webhook_url" python3 weather_video.py
```

## Step 3: Test the Setup

Run the script manually to test:

```bash
cd /Users/yvorolefes/Desktop/Weersverwachtingetjes
python3 weather_video.py
```

If everything works, you should see:
- Video generated as `weer_vandaag.mp4`
- Message: "✅ Video successfully posted to Discord!"

## Step 4: Schedule Automatic Posts (macOS)

### Using launchd (Recommended for macOS)

1. **Update the plist file** (`com.yourname.weather-discord.plist`):
   - Replace `com.yourname` with your preferred identifier
   - Adjust the times in `StartCalendarInterval` (currently set to 8:00 AM and 6:00 PM)

2. **Make the script executable**:
   ```bash
   chmod +x schedule_discord_posts.sh
   ```

3. **Load the launchd service**:
   ```bash
   launchctl load ~/Library/LaunchAgents/com.yourname.weather-discord.plist
   ```

   Or copy the plist to the LaunchAgents directory first:
   ```bash
   cp com.yourname.weather-discord.plist ~/Library/LaunchAgents/
   launchctl load ~/Library/LaunchAgents/com.yourname.weather-discord.plist
   ```

4. **Check if it's loaded**:
   ```bash
   launchctl list | grep weather-discord
   ```

5. **To unload (if needed)**:
   ```bash
   launchctl unload ~/Library/LaunchAgents/com.yourname.weather-discord.plist
   ```

### Using cron (Alternative)

1. **Open crontab**:
   ```bash
   crontab -e
   ```

2. **Add these lines** (adjust paths and times as needed):
   ```cron
   # Post weather video at 8:00 AM and 6:00 PM daily
   0 8 * * * cd /Users/yvorolefes/Desktop/Weersverwachtingetjes && /usr/bin/python3 weather_video.py >> schedule.log 2>&1
   0 18 * * * cd /Users/yvorolefes/Desktop/Weersverwachtingetjes && /usr/bin/python3 weather_video.py >> schedule.log 2>&1
   ```

3. **Save and exit**

## Troubleshooting

### Video file too large (>25MB)
Discord webhooks have a 25MB file size limit. If your videos are larger:
- Consider compressing the video
- Use a Discord bot instead (requires bot token)
- Upload to a file hosting service and post the link

### Webhook not working
- Verify the webhook URL is correct
- Check if the webhook was deleted in Discord
- Check the logs: `schedule_error.log` and `schedule_output.log`

### Script not running on schedule
- Check launchd logs: `log show --predicate 'process == "launchd"' --last 1h`
- Verify the plist file is loaded: `launchctl list | grep weather`
- Test the script manually first

### Disable Discord posting temporarily
Run with the `--no-discord` flag:
```bash
python3 weather_video.py --no-discord
```

## File Size Note

If your videos consistently exceed 25MB, you may want to:
1. Reduce video quality/bitrate in the `create_video` function
2. Use a Discord bot with higher file size limits
3. Compress videos before uploading
