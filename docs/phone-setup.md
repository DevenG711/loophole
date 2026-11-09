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

You make this once. It covers every app you pick, and it sends the name of
whichever app you opened.

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
    - Tap **Add new field** > **Text**. Set the key to `app`. For the value,
      tap the text box and pick **Shortcut Input** from the bar above the
      keyboard. Shortcut Input is the app that was just opened, so Loophole
      gets its name (for example `Instagram`).
11. Tap **Done** (top right).

That's it. To watch more apps later, edit this automation and tick them in
step 4. You don't need a new automation.

### Which apps Loophole knows by name

Loophole can tell BehaviorGPT about these apps (capitals don't matter):

`Instagram`, `TikTok`, `YouTube`, `X`, `Reddit`, `Snapchat`, `Facebook`,
`WhatsApp`, `Messages`, `Discord`, `Pinterest`, `LinkedIn`, `Netflix`,
`Twitch`, `Spotify`, `Robinhood`, `Chess`

Any other app you tick still counts as a pickup. Loophole just can't tell
BehaviorGPT what the app was.

If the **Last phone** line (see step 3) shows `Unknown` instead of the app's
name, the value in step 10 is empty: edit the automation and pick
**Shortcut Input** again. Pickups still count while you fix it.

## 3. Test it

1. On the Mac, start Loophole (`bash start.sh`), click the dot and pick
   **Start focus session**. The laptop only listens during a session.
2. On the iPhone, open one of the apps you ticked.
3. The first time, iOS may ask **Allow "…" to connect to ntfy.sh?** Tap
   **Always Allow**. Without it the automation stops and asks every time.
4. Within about 10 seconds, click the Loophole dot. The **Last phone** line
   should say something like "Last phone: Instagram, 0 min ago".

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
