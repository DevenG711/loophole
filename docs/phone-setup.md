# Phone setup (iPhone, about 5 minutes)

This step is optional, but it's how Loophole learns. Each time you open a
time-wasting app on your iPhone, a Shortcuts automation sends that app's name
to your private phone link. The laptop checks the link every 10 seconds and
logs a "pickup". Loophole learns what the minute before your pickups looked
like, and starts nudging after about 5 of them.

The phone and the laptop don't need to be on the same Wi-Fi. Both just need
the internet.

## 1. Get your phone link

`bash setup.sh` printed it and copied it to your clipboard. It looks like this:

```
https://ntfy.sh/loophole-0c107f5ee0d839ec
```

Lost it? Click the Loophole dot in the menu bar > **Phone setup** >
**Copy phone link**. Or open `.env` and put `https://ntfy.sh/` in front of the
`NTFY_TOPIC` value.

Getting it onto the phone: if your iPhone and Mac use the same Apple ID, copy
it on the Mac and paste on the phone (Universal Clipboard). If not, AirDrop it
or send it to yourself in Notes.

Keep the link private. Anyone who has it can see your pickups or send fake
ones.

## 2. Make one automation for all your apps

You make this once, and it covers every app you tick.

1. Open the **Shortcuts** app and tap the **Automation** tab at the bottom.
2. Tap **+** (top right). If you have no automations yet, tap **New Automation**.
3. Scroll down and tap **App**.
4. Tap **Choose** and tick every app that pulls you away from work (for
   example Instagram, TikTok, YouTube, X, Reddit, Snapchat). Tap **Done**.
5. Make sure **Is Opened** is ticked and **Is Closed** is not.
6. Choose **Run Immediately**. Turn **Notify When Run** off. Tap **Next**.
7. Tap **New Blank Automation**.
8. Tap **Add Action**, search for **Get Contents of URL** and tap it.
9. Tap the blue **URL** word and paste your phone link.
10. Tap the **>** arrow next to the action to show more options, and set:
    - **Method:** `POST`
    - **Request Body:** `Form`
    - Tap **Add new field** > **Text**. Set the key to `app` and the text to
      `Phone`.
11. Tap **Done** (top right).

To watch more apps later, edit this automation and tick them in step 4.

iOS doesn't tell the automation which of the ticked apps was opened, so every
pickup arrives as "Phone". That's all Loophole needs to learn when you reach
for your phone and to nudge you. What you lose: Loophole can't tell
BehaviorGPT which phone app it was, and nudges say "your phone" instead of
"TikTok on your phone".

### Optional: send the app's name

If you want the app's name, make one automation per app instead: in step 4
tick just one app, and in step 10 type that app's name instead of `Phone`.
Repeat for each app. You can mix both: separate automations for your top one
or two apps, and the all-apps one for the rest (don't tick an app in both, or
each pickup counts twice).

Use these names so Loophole knows which app it was (capitals don't matter):

`Instagram`, `TikTok`, `YouTube`, `X`, `Reddit`, `Snapchat`, `Facebook`,
`WhatsApp`, `Messages`, `Discord`, `Pinterest`, `LinkedIn`, `Netflix`,
`Twitch`, `Spotify`, `Robinhood`, `Chess`

## 3. Test it

1. On the Mac, start Loophole (`bash start.sh`), click the dot and pick
   **Start focus session**. The laptop only listens during a session.
2. On the iPhone, open one of the apps you ticked.
3. The first time, iOS may ask **Allow "…" to connect to ntfy.sh?** Tap
   **Always Allow**. Without it the automation stops and asks every time.
4. Within about 10 seconds, click the Loophole dot. The **Last phone** line
   should say something like "Last phone: Phone, 0 min ago".

Still nothing? Open your phone link in a browser on the Mac. Each pickup shows
up there as a message.

- **No message on that page:** the phone isn't sending. Check the link in the
  automation (no space at the end), that the method is `POST`, and that
  **Run Immediately** is on.
- **Message there, but no "Last phone" in the menu:** the Mac isn't listening.
  Make sure a focus session is running, and that `NTFY_TOPIC` in `.env` matches
  the end of the link. If you edited `.env`, quit and restart Loophole.

You can also check the laptop side without the phone: **Phone setup** >
**Send test ping**. During a session the **Last phone** line shows
"Test (test ping)". Test pings never count as pickups.

## Changing apps later

Edit the automation in Shortcuts > **Automation** and tick or untick apps. If
you change `NTFY_TOPIC` in `.env`, update the URL in the automation too.
