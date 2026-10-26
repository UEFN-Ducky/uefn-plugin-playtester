---
name: playtester
description: "Play Tester: play test games in game. Jev drives the player (walk to, use, check, click) about once a second in Fortnite (a UEFN session), Godot, Unreal, Roblox, web games or any game window, several testers at once, one tab each."
keywords: play test, playtest, in game, test the island, test my game, fortnite client, godot, unreal, roblox, web game, game window, walk the player, move the character, jev, vision, bot, qa, automated test, several testers, parallel
license: MIT
metadata:
  label: Play Tester
  version: 1
  author: UEFN-Ducky
  source_plugin_id: playtester
---

# Play Tester: hand in-game testing to Jev

Play Tester plays the **real game**: the Fortnite client a UEFN session launches, and any other
game through a tester (Godot, Unreal, Roblox, a web page, any window). Each step it captures the
game, asks Jev (TypeSafe's fast vision model) several typed questions about the frame in one call,
decides the move in plain code, and presses the keys and moves the mouse. A step takes about a
second, so a play test takes seconds, not minutes. The person watches every key, mouse turn and
Jev answer live in the **Play Tester** tab (header button).

**Use it instead of looking at screenshots yourself.** You give the goal and read the result; you
do not capture and inspect every frame. Reach for `fortnite_client_capture` only when you need to
see a picture with your own eyes.

## Other games and several testers

Every tool takes `tester`. Empty means the tester you used last in this chat, else **default**,
the Fortnite client, so Fortnite play tests need nothing new. For another game, open a tester
first; it becomes a tab in Play Tester:

1. `playtester_targets()`: presets and the open game windows (title, program, size).
2. `playtester_open(target=..., profile=...)` gives `tester.id`:
   - `godot[:<project folder>]`: a Godot game run from the editor with the Godot plugin's runtime
     probe (`godot_call(tool="install_runtime_probe")` once, then `godot_call(tool="run_project")`).
     Frames and input go through the probe, so it never needs the window in front. `player=<node
     path>` lets `playtester_where(target_actor=<node path>)` read positions.
   - `unreal[:<exe>]`: a packaged build (`...-Win64-Shipping.exe`) or the editor's play window.
   - `roblox`: the Roblox player (camera turns with the right mouse button held).
   - `web:<url>` (or `web:localhost:<port>`): opens a Browser tab; runs in the background.
   - `window:<exe or part of the title>`: any other game window.
   - `profile`: `fps`, `tps`, `roblox`, `side2d` (platformer: a turn only changes which way W
     walks), `topdown`, `pointclick` (Jev boxes what to click and it is clicked). `keys` maps moves
     to the game's keys: `"SPACE=ARROWUP, E=F"`. `game_name` is what Jev calls the game.
3. Pass `tester=<id>` to `playtester_run`, `playtester_check`, `playtester_press` and the rest.
4. `playtester_close(tester)` when done (its runs are forgotten; Fortnite's tester stays).

Several testers run **at the same time**, one run each. Games in desktop windows share the one
keyboard and mouse, so they take turns step by step (their tab says *waiting*); Godot and web
testers never wait. Each tester has its own Who answers and mouse speed. World-coordinate steering
(beacon, `target_actor`, `success_print`, `playtester_map`) is Fortnite's; other games steer by the
picture.

## When to use it

- The person asks to test something **in game**: "walk to the shop and buy a weapon", "check the
  door opens", "does the jump pad work", "play test the island", "make sure the button gives a coin".
- A UEFN session is running (Launch Session / Start Game) and its Fortnite client is in the island.
- After you changed devices or Verse and pushed the changes, to check them in play.

## Steps

1. `playtester_status()`: `ready` must be true. Otherwise its `next` says what is missing (no
   TypeSafe key, no Fortnite client, still loading). Say that to the person; don't press keys.
   If `beacon.on` is false, set the beacon up before the play test (it stays in the level, so this
   happens once per island): `playtester_beacon(add=true)` (does nothing when the file is already
   there), build Verse, push changes, make sure one `playtester_beacon_device` is placed in the level,
   then launch the session again. Tell the person in one line that you are adding it.
2. `playtester_run(goal, target, success, interact)`:
   - `goal`: one plain sentence ("walk to the vending machine and use it").
   - `target`: the thing to walk to, as you would point at it ("the vending machine", "the red
     button on the wall"). Leave empty to let Jev pick each move from the goal.
   - `success`: a yes/no question about the screen that is true when the test passed ("Is a weapon
     in the hotbar?", "Is the door open?"). The run succeeds when it holds on two frames.
   - `interact=True` to hold E at the target.
   - `dry_run=True` to watch what it would do without pressing anything.
   It waits up to 25 s. When `finished` is false, call `playtester_wait(run_id)` until it is.
3. Read `status` (`success`, `failed`, `blocked`, `timeout`, `stopped`, `done`), `reason`,
   `moves` (one line per step) and `last_why`. Report them plainly. Call `playtester_frame` only
   if you need to see the last frame.

## World coordinates: behind the player, behind buildings, other floors

Jev only sees what is on screen. For a target that is behind the player, behind a building or
upstairs, also give its position so Play Tester steers by coordinates until Jev can see it:

- `target_actor="<the actor's label in the level>"` (looked up in UEFN once at the start), or
  `target_xyz="x,y,z"` in centimetres.
- The island needs the **Play Tester beacon** device, which prints the player's position and facing
  to the UEFN Output Log: `playtester_beacon()` says whether it is on; `playtester_beacon(add=true)`
  adds its Verse file (`Content/Verse/PlayTester/playtester_beacon_device.verse`); then build Verse, push
  changes, place `playtester_beacon_device` in the level and launch the session.
- With both, each step knows the distance, the turn (behind = turn first), the height (more than
  2.5 m = another floor: it looks for stairs, a ramp or a ladder) and goes around walls with Jev's
  path answer; the last metres use Jev's box. It also learns the mouse speed from real turns.
- `playtester_where(target_actor)` answers "34 m away, behind you (150° right), 4.2 m up".
- Without the beacon a run still works by the image alone; the result says so in `note`.

## Verse prints: numbers instead of pictures

Play Tester reads the island's Verse `Print` lines from the UEFN Output Log while it plays:

- `[PTPOS] tick player x y z yaw pitch state` from the beacon: position, facing, and movement
  state (ground, air, falling, gliding, water). Each step records height, climb since the last
  step and state; Jev is told "on ground, height 2.4 m, climbing 0.6 m".
- `[PTMARK] name x y z`: landmarks. The beacon prints the props listed under its Landmarks /
  LandmarkNames; any device can print its own. The three nearest go to Jev and onto the tab map.
- `[PTEVENT] text` or any other print: `playtester_run(success_print="coin collected")` passes the
  moment a new print contains that text, exact and instant, better than asking Jev about the
  screen. Prints seen during a run are in its events.

When you write Verse for the person's island, add a `Print("[PTEVENT] …")` where the thing to test
happens, then test with `success_print`.

## The island's layout

`playtester_map()` reads every device's label and position from UEFN once and keeps it (refresh=true
reads it again). Runs use it for nearby landmarks and to find a `target_actor` when the editor is busy.

## Walking smoothly

With "Keep walking while Jev thinks" (on), a forward walk stays held between steps and turns happen
while walking; the player stops only to use something, wait, turn sharply or when the run ends. Keys
come up the moment Fortnite loses focus, Stop is pressed, or no decision arrives for 2.5 s.

## Learning from past runs

Every run leaves a lesson (outcome, steps, totals, which rule moved the player, where the target
was found and reached, what blocked it) and updates a memory per island and target.

- Before testing a target that failed before, read `playtester_lessons(target)`: success rate,
  average steps, blockers, tips. `playtester_lessons()` lists every target tested on the island.
- The next run of the same target starts from memory by itself: its first scan turns toward the
  side the target was found last time, and with the beacon it heads for where it was reached.
  The run's start result names it in `memory_used`, and `warn` says when menus stopped earlier runs.
- Results carry `totals` (turns, metres walked, times stuck, uses, step of first sight, false boxes)
  and `focus_events`: report the useful ones, such as "found on step 3, stuck once".

## The game in front

Play Tester brings a desktop game to the front by itself before pressing anything: Windows' switch, a
stronger switch, then (setting "Click to bring the game to the front", on by default) one click on
the title bar, or a right click (aim) in the middle when fullscreen, with the cursor put back. If it
still can't, the run stops with "click the game window once": ask the person to do that.

## Other tools

- `playtester_check(questions)`: Jev answers your own questions about the current frame in one
  call (types `noul`, `choice`, `score`, `box`; the frame is `frame`). Fast checks without moving.
- `playtester_find(target)`: is it on screen, which side, and its box.
- `playtester_press(keys, seconds)`, `playtester_look(dx_degrees, dy_degrees)`,
  `playtester_click(button, x, y)`: single moves (W/A/S/D, SPACE jump, E use, SHIFT, CTRL, digits,
  ARROWUP...; the tester's key map applies). `x`, `y` (0..1) click a point: menus, point and click.
- `playtester_testers()`: every open tester, what it plays, its running run, waiting or not.
- `playtester_stop()`: stop a run now; every key is released.
- `playtester_calibrate(turn_degrees=20)`: measure the mouse speed once if turns overshoot or fall short.
- `playtester_get_run`, `playtester_list_runs`: what happened, step by step.

## Rules

- One run at a time per tester; testers run side by side. Never press keys on a tester while its
  run is playing.
- Play Tester never sends input while the game is loading; don't try to get around it.
- `blocked` means a menu or popup covers the game: ask the person to close it (or press ESC with
  `playtester_press` only when they say so).
- Box answers are always a box: trust them only when `seen` (or `found`) is high.

## Examples

- "Test the vending machine": `playtester_run(goal="walk to the vending machine and use it",
  target="the vending machine", success="Is a weapon or item shown in the hotbar?", interact=True)`.
- "Does the jump pad work?": `playtester_run(goal="walk onto the jump pad", target="the jump pad",
  success="Is the player in the air high above the ground?")`.
- "Go press the button upstairs in the tower": `playtester_run(goal="go up the tower and press the
  button", target="the button", target_actor="Button_Tower", interact=True)`.
- "Is the score UI showing?": `playtester_check({"ui": {"type": "noul", "instructions": "Does
  `frame` show a score counter on screen?"}})`.
- "Test my Godot platformer's first level": `playtester_open(target="godot", label="Pixel Quest",
  profile="side2d")` -> `tester.id` is `"pixel-quest"`, then `playtester_run(goal="reach the flag at
  the end of the level", target="the flag", tester="pixel-quest")`.
- "Test the build and the web export together": `playtester_open(target="window:PixelQuest.exe",
  profile="side2d")` and `playtester_open(target="web:localhost:8060", profile="side2d")`, then
  `playtester_run(..., wait_s=0, tester=...)` on both and `playtester_wait(run_id, tester=...)` each.
- "Click Start in the menu": `playtester_open(target="window:MyGame.exe", profile="pointclick")`,
  then `playtester_run(goal="start a new game", target="the Start button", tester=...)`.

## Workflow nodes

`playtester.open` (outputs the tester id; reuses a tester with the same tab name),
`playtester.status`, `playtester.run`, `playtester.check`, `playtester.press`, `playtester.look`,
`playtester.stop`, `playtester.where`, `playtester.lessons`, `playtester.map`: each takes `tester`
(config or input pin). Template **Play test a goal**.
