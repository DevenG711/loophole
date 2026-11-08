"""The notch island: an always-on-top black pill at the top centre of the
screen, like the iPhone Dynamic Island.

- Collapsed, it hugs the menu bar. On a MacBook with a notch it sits around
  the hardware notch: status dot on the left "ear", timer on the right ear.
- Hover it (or a nudge arrives) and it grows down into a panel with the
  details and buttons.

Everything is drawn by hand in one NSView (no Quartz/CoreAnimation needed).
A 60 fps NSTimer eases the size toward its target and redraws; it stops when
nothing moves. The menu-bar app owns all the state: it calls
`Island.update(view)` once a second with a dict from `menubar.island_view()`,
and the island calls `on_action(id)` when a button is clicked.
"""
from __future__ import annotations

import math
import time

import objc
from AppKit import (
    NSBackingStoreBuffered, NSBezierPath, NSColor, NSFont, NSGraphicsContext,
    NSFontAttributeName, NSForegroundColorAttributeName, NSMakeRect,
    NSPanel, NSParagraphStyleAttributeName, NSMutableParagraphStyle,
    NSRunLoop, NSRunLoopCommonModes, NSScreen, NSShadow, NSString,
    NSTimer, NSTrackingArea, NSView,
)

# AppKit constants (spelled out so we don't depend on which names PyObjC exports)
BORDERLESS = 0
NONACTIVATING = 1 << 7
STATUS_LEVEL = 25                      # NSStatusWindowLevel: above the menu bar
ALL_SPACES = 1 << 0                    # NSWindowCollectionBehaviorCanJoinAllSpaces
STATIONARY = 1 << 4
IGNORES_CYCLE = 1 << 6
FULLSCREEN_AUX = 1 << 8
TRACK_ENTER_EXIT = 0x01
TRACK_MOVED = 0x02
TRACK_ALWAYS = 0x80
TRACK_VISIBLE_RECT = 0x200
MOVE_FPS = 60    # while the island grows or shrinks
PULSE_FPS = 15   # while it only pulses (a nudge waiting for an answer)
WORD_WRAP = 0

# Look
SHOULDER = 7.0          # concave flare where the pill meets the top edge
EXPANDED_WIDTH = 420.0
PAD = 22.0
EASE = 0.24             # fraction of the remaining distance covered per frame
COLLAPSE_DELAY = 0.35   # seconds after the mouse leaves before collapsing

COLOURS = {             # Apple's dark-mode system colours
    "green": (0.19, 0.82, 0.35),
    "amber": (1.00, 0.84, 0.04),
    "red": (1.00, 0.27, 0.23),
    "blue": (0.39, 0.82, 1.00),
    "grey": (0.56, 0.56, 0.58),
}


def _rgb(name, alpha=1.0):
    r, g, b = COLOURS.get(name, COLOURS["grey"])
    return NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, alpha)


def _white(alpha):
    return NSColor.colorWithWhite_alpha_(1.0, alpha)


def _font(size, weight=0.0, mono_digits=False):
    if mono_digits:
        return NSFont.monospacedDigitSystemFontOfSize_weight_(size, weight)
    return NSFont.systemFontOfSize_weight_(size, weight)


SEMIBOLD, MEDIUM, REGULAR = 0.3, 0.23, 0.0


def _attrs(font, colour, wrap=False):
    attrs = {NSFontAttributeName: font, NSForegroundColorAttributeName: colour}
    if wrap:
        style = NSMutableParagraphStyle.alloc().init()
        style.setLineBreakMode_(WORD_WRAP)
        style.setLineSpacing_(2.0)
        attrs[NSParagraphStyleAttributeName] = style
    return attrs


def _text_size(text, attrs):
    return NSString.stringWithString_(text).sizeWithAttributes_(attrs)


def _wrapped_height(text, attrs, width):
    rect = NSString.stringWithString_(text).boundingRectWithSize_options_attributes_(
        (width, 10000), 1 | 2, attrs)   # UsesLineFragmentOrigin | UsesFontLeading
    return math.ceil(rect.size.height)


def _round_rect(x, y, w, h, r):
    return NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(x, y, w, h), r, r)


class IslandPanel(NSPanel):
    def canBecomeKeyWindow(self):
        return False

    def canBecomeMainWindow(self):
        return False

    def constrainFrameRect_toScreen_(self, frame, screen):
        return frame   # allow sitting over the menu bar


class IslandView(NSView):
    """Draws the island and handles hover and clicks. Coordinates are flipped
    (y grows downward from the top edge of the screen)."""

    def isFlipped(self):
        return True

    def acceptsFirstMouse_(self, _event):
        return True

    # ---- state set by Island ---------------------------------------------
    @objc.python_method
    def setup(self, island):
        self.island = island
        self.hover_button = None
        self.area = None
        self.updateTrackingAreas()

    def updateTrackingAreas(self):
        objc.super(IslandView, self).updateTrackingAreas()
        if getattr(self, "area", None) is not None:
            self.removeTrackingArea_(self.area)
        self.area = NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(), TRACK_ENTER_EXIT | TRACK_MOVED | TRACK_ALWAYS | TRACK_VISIBLE_RECT,
            self, None)
        self.addTrackingArea_(self.area)

    def step_(self, _timer):
        """Animation timer callback (MOVE_FPS or PULSE_FPS); forwards to the Island."""
        self.island.step()

    # ---- mouse -------------------------------------------------------------
    def mouseEntered_(self, _event):
        self.island.set_hover(True)

    def mouseExited_(self, _event):
        self.hover_button = None
        self.island.set_hover(False)

    def mouseMoved_(self, event):
        hit = self.button_at(event)
        if hit != self.hover_button:
            self.hover_button = hit
            self.setNeedsDisplay_(True)

    def mouseDown_(self, event):
        hit = self.button_at(event)
        if hit:
            self.island.click(hit)
        else:
            self.island.set_hover(True)   # a click on the pill also opens it

    @objc.python_method
    def button_at(self, event):
        p = self.convertPoint_fromView_(event.locationInWindow(), None)
        if self.island.progress < 0.9:
            return None
        for bid, (x, y, w, h) in self.island.button_rects:
            if x <= p.x <= x + w and y <= p.y <= y + h:
                return bid
        return None

    # ---- drawing -----------------------------------------------------------
    def drawRect_(self, _rect):
        isl = self.island
        b = self.bounds()
        w, h = b.size.width, b.size.height
        p = isl.progress
        s = SHOULDER
        r = isl.collapsed_radius + (26.0 - isl.collapsed_radius) * p
        r = min(r, (h - 1) / 2, (w - 2 * s) / 2)

        # The shape: flared shoulders at the top, rounded bottom corners.
        path = NSBezierPath.bezierPath()
        k = 0.45   # control-point pull (smooth, "squircle-ish" corners)
        path.moveToPoint_((0, 0))
        path.curveToPoint_controlPoint1_controlPoint2_((s, s), (s * 0.6, 0), (s, s * 0.4))
        path.lineToPoint_((s, h - r))
        path.curveToPoint_controlPoint1_controlPoint2_((s + r, h), (s, h - r * k), (s + r * k, h))
        path.lineToPoint_((w - s - r, h))
        path.curveToPoint_controlPoint1_controlPoint2_((w - s, h - r), (w - s - r * k, h), (w - s, h - r * k))
        path.lineToPoint_((w - s, s))
        path.curveToPoint_controlPoint1_controlPoint2_((w, 0), (w - s, s * 0.4), (w - s * 0.4, 0))
        path.closePath()
        NSColor.blackColor().setFill()
        path.fill()
        path.addClip()

        collapsed_alpha = max(0.0, 1.0 - p * 2.5)
        expanded_alpha = max(0.0, (p - 0.4) / 0.6)
        if collapsed_alpha > 0:
            self.draw_collapsed(w, collapsed_alpha)
        if expanded_alpha > 0:
            self.draw_expanded(w, expanded_alpha)

    @objc.python_method
    def draw_dot(self, cx, cy, radius, colour, alpha):
        isl = self.island
        if isl.view.get("pulse"):
            alpha *= 0.55 + 0.45 * (0.5 + 0.5 * math.sin(time.time() * 5.0))
        glow = NSShadow.alloc().init()
        glow.setShadowColor_(_rgb(colour, 0.9 * alpha))
        glow.setShadowBlurRadius_(radius * 1.6)
        glow.setShadowOffset_((0, 0))
        NSGraphicsContext.saveGraphicsState()
        glow.set()
        _rgb(colour, alpha).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(cx - radius, cy - radius, radius * 2, radius * 2)).fill()
        NSGraphicsContext.restoreGraphicsState()

    @objc.python_method
    def draw_collapsed(self, w, alpha):
        isl, v = self.island, self.island.view
        h = isl.collapsed_h
        cy = h / 2
        # Left ear: just the dot (colour says the state)
        self.draw_dot(SHOULDER + 12 + 4, cy, 4.0, v["colour"], alpha)
        # Right ear: timer
        value = v.get("value") or ""
        if value:
            attrs = _attrs(_font(12.5, SEMIBOLD, mono_digits=True), _white(0.92 * alpha))
            size = _text_size(value, attrs)
            NSString.stringWithString_(value).drawAtPoint_withAttributes_(
                (w - SHOULDER - 12 - size.width, cy - size.height / 2), attrs)

    @objc.python_method
    def draw_expanded(self, w, alpha):
        isl, v = self.island, self.island.view
        colour = v["colour"]
        x0 = SHOULDER + PAD
        inner = w - 2 * x0
        y = isl.content_top()

        # Header: small dot + headline, momentum bars on the right
        head_attrs = _attrs(_font(16, SEMIBOLD), _white(alpha))
        head = v.get("headline", "")
        hs = _text_size(head, head_attrs)
        self.draw_dot(x0 + 4, y + hs.height / 2, 4.0, colour, alpha)
        NSString.stringWithString_(head).drawAtPoint_withAttributes_((x0 + 16, y), head_attrs)
        bars = v.get("bars") or []
        if bars:
            self.draw_bars(x0 + inner, y + hs.height / 2, bars, colour, alpha)
        y += hs.height + 4

        # Sub-line (wraps)
        sub = v.get("sub", "")
        if sub:
            sub_attrs = _attrs(_font(12.5, REGULAR), _white(0.66 * alpha), wrap=True)
            sh = _wrapped_height(sub, sub_attrs, inner)
            NSString.stringWithString_(sub).drawWithRect_options_attributes_(
                NSMakeRect(x0, y, inner, sh), 1 | 2, sub_attrs)
            y += sh + 10

        # Progress bar
        prog = v.get("progress")
        if prog is not None:
            _white(0.12 * alpha).setFill()
            _round_rect(x0, y, inner, 4, 2).fill()
            _rgb(colour, alpha).setFill()
            _round_rect(x0, y, max(4.0, inner * max(0.0, min(1.0, prog))), 4, 2).fill()
            y += 4 + 10

        # Detail lines
        det_attrs = _attrs(_font(11.5, REGULAR), _white(0.42 * alpha), wrap=True)
        for line in v.get("details") or []:
            dh = _wrapped_height(line, det_attrs, inner)
            NSString.stringWithString_(line).drawWithRect_options_attributes_(
                NSMakeRect(x0, y, inner, dh), 1 | 2, det_attrs)
            y += dh + 3

        # Buttons
        isl.button_rects = []
        buttons = v.get("buttons") or []
        if buttons:
            y += 8
            gap = 8.0
            bw = (inner - gap * (len(buttons) - 1)) / len(buttons)
            for i, (bid, title, primary) in enumerate(buttons):
                bx = x0 + i * (bw + gap)
                hover = self.hover_button == bid
                if primary and colour == "grey":
                    fill = _white((1.0 if hover else 0.9) * alpha)
                    text_colour = NSColor.colorWithWhite_alpha_(0.0, 0.88 * alpha)
                elif primary:
                    fill = _rgb(colour, (1.0 if hover else 0.9) * alpha)
                    text_colour = NSColor.colorWithWhite_alpha_(0.0, 0.88 * alpha)
                else:
                    fill = _white((0.22 if hover else 0.13) * alpha)
                    text_colour = _white(0.95 * alpha)
                fill.setFill()
                _round_rect(bx, y, bw, 30, 15).fill()
                attrs = _attrs(_font(12.5, MEDIUM), text_colour)
                ts = _text_size(title, attrs)
                NSString.stringWithString_(title).drawAtPoint_withAttributes_(
                    (bx + (bw - ts.width) / 2, y + (30 - ts.height) / 2), attrs)
                isl.button_rects.append((bid, (bx, y, bw, 30)))

    @objc.python_method
    def draw_bars(self, right, cy, bars, colour, alpha):
        bw, gap, tall = 3.0, 2.5, 16.0
        top = max(bars) or 1
        x = right - len(bars) * (bw + gap) + gap
        for i, value in enumerate(bars):
            bh = max(2.5, tall * value / top) if top else 2.5
            recent = i >= len(bars) - 2
            c = _rgb(colour, (0.95 if recent else 0.45) * alpha) if value else _white(0.18 * alpha)
            c.setFill()
            _round_rect(x, cy + tall / 2 - bh, bw, bh, bw / 2).fill()
            x += bw + gap


class Island:
    """Owns the panel and the animation. Main thread only."""

    def __init__(self, on_action):
        self.on_action = on_action
        self.view = {"colour": "grey", "value": "", "headline": "",
                     "buttons": []}
        self.hover = False
        self.leave_at: float | None = None
        self.progress = 0.0           # 0 = collapsed, 1 = expanded
        self.button_rects: list = []
        self.collapsed_radius = 10.0
        self._measure_screen()
        self.cur_w, self.cur_h = self.collapsed_size()

        self.panel = IslandPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, self.cur_w, self.cur_h), BORDERLESS | NONACTIVATING,
            NSBackingStoreBuffered, False)
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setHasShadow_(False)
        self.panel.setLevel_(STATUS_LEVEL)
        self.panel.setCollectionBehavior_(ALL_SPACES | STATIONARY | IGNORES_CYCLE | FULLSCREEN_AUX)
        self.panel.setHidesOnDeactivate_(False)
        self.panel.setFloatingPanel_(True)
        self.panel.setAcceptsMouseMovedEvents_(True)

        self.canvas = IslandView.alloc().initWithFrame_(NSMakeRect(0, 0, self.cur_w, self.cur_h))
        self.canvas.setup(self)
        self.panel.setContentView_(self.canvas)
        self._place()
        self.panel.orderFrontRegardless()

        self.timer = None
        self.timer_fps = 0

    # ---- geometry --------------------------------------------------------
    def _measure_screen(self):
        screens = NSScreen.screens()
        self.screen = screens[0] if screens and len(screens) else NSScreen.mainScreen()
        frame = self.screen.frame()
        self.notch_w = 0.0
        top = 0.0
        try:
            top = self.screen.safeAreaInsets().top
            if top > 0:
                left = self.screen.auxiliaryTopLeftArea().size.width
                right = self.screen.auxiliaryTopRightArea().size.width
                self.notch_w = max(0.0, frame.size.width - left - right)
        except Exception:
            pass
        menu_bar = frame.origin.y + frame.size.height - (
            self.screen.visibleFrame().origin.y + self.screen.visibleFrame().size.height)
        self.collapsed_h = float(top) if top > 0 else max(26.0, float(menu_bar) + 2)
        self.collapsed_radius = min(10.0, self.collapsed_h / 2 - 1)

    def content_top(self):
        """Hardware notch hides the top strip, so start below it there."""
        return (self.collapsed_h if self.notch_w else 8.0) + 10

    def collapsed_size(self):
        v = self.view
        value_w = _text_size(v.get("value") or "", _attrs(_font(12.5, SEMIBOLD, True), _white(1))).width
        left = 12 + 8 + 8                                  # pad + dot + gap
        right = 8 + value_w + 12 if value_w else 12
        if not self.notch_w:   # no notch: dot and timer sit side by side
            return left + right + 2 * SHOULDER, self.collapsed_h
        ear = max(left, right)   # notch is centred, so both ears match
        middle = self.notch_w
        return middle + 2 * ear + 2 * SHOULDER, self.collapsed_h

    def expanded_size(self):
        v = self.view
        w = max(EXPANDED_WIDTH, self.collapsed_size()[0])
        inner = w - 2 * (SHOULDER + PAD)
        h = self.content_top()
        h += _text_size(v.get("headline") or " ", _attrs(_font(16, SEMIBOLD), _white(1))).height + 4
        if v.get("sub"):
            h += _wrapped_height(v["sub"], _attrs(_font(12.5), _white(1), wrap=True), inner) + 10
        if v.get("progress") is not None:
            h += 14
        det = _attrs(_font(11.5), _white(1), wrap=True)
        for line in v.get("details") or []:
            h += _wrapped_height(line, det, inner) + 3
        if v.get("buttons"):
            h += 8 + 30
        return w, h + 18

    def _target(self):
        pinned = bool(self.view.get("pinned"))
        want_open = pinned or self.hover
        tp = 1.0 if want_open else 0.0
        tw, th = self.expanded_size() if want_open else self.collapsed_size()
        return tp, tw, th

    def _place(self):
        frame = self.screen.frame()
        x = frame.origin.x + (frame.size.width - self.cur_w) / 2
        y = frame.origin.y + frame.size.height - self.cur_h
        self.panel.setFrame_display_(NSMakeRect(round(x), round(y),
                                                round(self.cur_w), round(self.cur_h)), True)
        self.canvas.setNeedsDisplay_(True)

    # ---- animation ---------------------------------------------------------
    def _kick(self, fps: int | None = None):
        """Run the step timer at `fps` (at least). Moving needs 60 fps; a
        panel that only pulses needs far less, so it doesn't burn CPU while a
        nudge waits for an answer."""
        fps = fps or MOVE_FPS
        if self.timer is not None and self.timer_fps >= fps:
            return
        if self.timer is not None:
            self.timer.invalidate()
        self.timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            1 / fps, self.canvas, "step:", None, True)
        self.timer_fps = fps
        NSRunLoop.currentRunLoop().addTimer_forMode_(self.timer, NSRunLoopCommonModes)

    def step(self):
        if self.leave_at is not None and time.time() >= self.leave_at:
            self.leave_at = None
            self.hover = False
        tp, tw, th = self._target()
        self.progress += (tp - self.progress) * EASE
        self.cur_w += (tw - self.cur_w) * EASE
        self.cur_h += (th - self.cur_h) * EASE
        settled = (abs(tp - self.progress) < 0.003 and abs(tw - self.cur_w) < 0.5
                   and abs(th - self.cur_h) < 0.5)
        if settled:
            self.progress, self.cur_w, self.cur_h = tp, tw, th
        self._place()
        if settled and self.leave_at is None:
            if not self.view.get("pulse"):
                self.timer.invalidate()
                self.timer, self.timer_fps = None, 0
            elif self.timer_fps > PULSE_FPS:   # only the glow moves now
                self.timer.invalidate()
                self.timer, self.timer_fps = None, 0
                self._kick(PULSE_FPS)

    # ---- API used by the app ---------------------------------------------
    def update(self, view: dict) -> None:
        self.view = view
        self._measure_screen()   # cheap; follows display changes
        self._kick()

    def set_hover(self, inside: bool) -> None:
        if inside:
            self.hover = True
            self.leave_at = None
        else:
            self.leave_at = time.time() + COLLAPSE_DELAY
        self._kick()

    def click(self, button_id: str) -> None:
        try:
            self.on_action(button_id)
        except Exception as e:
            print(f"[loophole] island action {button_id} failed: {e!r}")

