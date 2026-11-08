# Phone setup (iPhone, about 5 minutes)

Loophole learns when you pick up your phone. An iPhone Shortcut sends the
app name to ntfy.sh each time you open a distracting app. The laptop checks
ntfy.sh every few seconds.

## 1. Pick a topic name

Your topic is set as `NTFY_TOPIC` in `.env`, for example
`loophole-k7q2x9m4p1`. Use the same name on the phone.

**Keep it random.** Anyone who knows the topic name can read it and post to it.
Do not use your name or anything easy to guess.

## 2. Make the automation

1. Open **Shortcuts** → **Automation** tab → **New Automation** (or **+**).
2. Choose **App**.
3. Tap **Choose** and pick **Instagram, TikTok, YouTube, X, Reddit, Snapchat**.
4. Tick **Is Opened**. Leave **Is Closed** off.
5. Choose **Run Immediately**. Turn **Notify When Run** off. Tap **Next**.
6. Tap **New Blank Automation**, then add the action **Get Contents of URL**.
7. Set the URL to `https://ntfy.sh/<NTFY_TOPIC>` (your topic name).
8. Tap the arrow to show more options:
   - **Method:** POST
   - **Request Body:** File
   - **File:** tap it and pick **Shortcut Input**, then tap the variable and
     change it to **Name** (the app name, e.g. "Instagram").
9. Tap **Done**.

**Simpler option:** if the Shortcut Input does not give a clean app name, make
one automation per app instead. Pick just that app in step 3, and set the
Request Body to **Text** with a fixed name, like `Instagram`.

The laptop only needs the app name as plain text, for example `Instagram`.

## 3. Test it

1. In the automation, tap the play button to run it once by hand.
2. If iOS asks "Allow ... to connect to ntfy.sh?", tap **Always Allow**.
   Without this, it will ask every time and the automation will not run silently.
3. Open Instagram (or any app you picked). The Loophole setup screen should
   show the pickup within about 10 seconds.

You can also open `https://ntfy.sh/<NTFY_TOPIC>` in a browser to see the
messages arrive.
