using { /Fortnite.com/Devices }
using { /Fortnite.com/Characters }
using { /Fortnite.com/Playspaces }
using { /Verse.org/Simulation }
using { /Verse.org/SceneGraph }
using { /UnrealEngine.com/Temporary/SpatialMath }

# Play Tester inbox: Ducky sends commands into a running session. Put this component on an entity
# (e.g. "PlayTesterInbox"). Ducky writes Inbox with live edit (about 0.1 s to arrive), this runs each
# command once and prints the answer. Live edit only: a published island never changes Inbox.
# Inbox: "<seq> <tool> <args...>" separated by ';' (seq only goes up; older ones are skipped).
# Answers: [PTREPLY] <seq> ok|fail <text>
#   ping                              -> ok pong
#   players                           -> ok <index>,<x>,<y>,<z>,<health> ...
#   teleport <player> <x> <y> <z> [yaw]
#   health <player> [value]           -> ok <health> (sets it first when value is given)
#   mark <text>                       -> prints [PTEVENT] <text> (a marker in the run's prints)
playtester_inbox_component := class<final_super>(component):

    @editable
    Inbox : string = ""

    var LastText : string = ""
    var DoneSeq : int = 0

    OnSimulate<override>()<suspends> : void =
        set LastText = Inbox  # ponytail: what was there at start is old, never rerun (no reply cache; the sender waits once)
        Print("[PTREPLY] 0 ok ready", ?Duration := 0.0)
        loop:
            if (Inbox <> LastText):
                set LastText = Inbox
                for (Line : PtSplit(LastText, ';')):
                    Words := PtSplit(Line, ' ')
                    if (SeqText := Words[0], Seq := PtParseInt[SeqText], Seq > DoneSeq, Tool := Words[1]):
                        set DoneSeq = Seq
                        Print("[PTREPLY] {Seq} {Run(Tool, Words)}", ?Duration := 0.0)
            Sleep(0.0)

    Run(Tool : string, Words : []string) : string =
        Players := if (Space := Entity.GetPlayspaceForEntity[]) then Space.GetPlayers() else array{}
        if (Tool = "ping"):
            "ok pong"
        else if (Tool = "players"):
            var Out : string = "ok"
            for (Index -> Player : Players, Character := Player.GetFortCharacter[]):
                P := Character.GetTransform().Translation
                set Out += " {Index},{P.X},{P.Y},{P.Z},{Character.GetHealth()}"
            Out
        else if (Tool = "teleport"):
            if:
                Who := PtParseInt[Words[2]]
                Player := Players[Who]
                Character := Player.GetFortCharacter[]
                PX := PtParseFloat[Words[3]]
                PY := PtParseFloat[Words[4]]
                PZ := PtParseFloat[Words[5]]
                Yaw := (if (Given := PtParseFloat[Words[6]]) then Given else 0.0)
                Character.TeleportTo[vector3{X := PX, Y := PY, Z := PZ}, MakeRotationFromYawPitchRollDegrees(Yaw, 0.0, 0.0)]
            then:
                "ok teleported {Who}"
            else:
                "fail teleport <player> <x> <y> <z> [yaw]: no such player, bad numbers or blocked"
        else if (Tool = "health"):
            if (Player := Players[PtParseInt[Words[2]]], Character := Player.GetFortCharacter[]):
                if (Value := PtParseFloat[Words[3]]):
                    Character.SetHealth(Value)
                "ok {Character.GetHealth()}"
            else:
                "fail health <player> [value]: no such player"
        else if (Tool = "mark"):
            var Text : string = ""
            for (Index -> Word : Words, Index >= 2):
                set Text = if (Text = "") then Word else "{Text} {Word}"
            Print("[PTEVENT] {Text}", ?Duration := 0.0)
            "ok marked"
        else:
            "fail unknown tool {Tool} (ping, players, teleport, health, mark)"

# Words of S between Separator (or new lines); empty pieces dropped.
PtSplit(S : string, Separator : char)<transacts> : []string =
    var Parts : []string = array{}
    var Current : string = ""
    for (C : S):
        if (C = Separator or C = '\n'):
            if (Current <> ""):
                set Parts += array{Current}
            set Current = ""
        else:
            set Current += array{C}
    if (Current <> ""):
        set Parts += array{Current}
    Parts

PtDigit(C : char)<transacts><decides> : int =
    var Found : int = -1
    for (Index -> D : "0123456789", D = C):
        set Found = Index
    Found >= 0
    Found

# "-12" -> -12. Fails on anything else (17 digits max, so never out of range).
PtParseInt(S : string)<transacts><decides> : int =
    S.Length > 0
    S.Length < 18
    var N : int = 0
    var Neg : logic = false
    var Bad : logic = false
    for (Index -> C : S):
        if (Index = 0, C = '-'):
            set Neg = true
        else if (D := PtDigit[C]):
            set N = N * 10 + D
        else:
            set Bad = true
    not Bad?
    if (Neg?) then -N else N

# "-12.5" -> -12.5 (no exponents). Fails on anything else.
PtParseFloat(S : string)<transacts><decides> : float =
    S.Length > 0
    S.Length < 30
    var Whole : float = 0.0
    var Frac : float = 0.0
    var Scale : float = 1.0
    var InFrac : logic = false
    var Neg : logic = false
    var Digits : int = 0
    var Bad : logic = false
    for (Index -> C : S):
        if (Index = 0, C = '-'):
            set Neg = true
        else if (C = '.', not InFrac?):
            set InFrac = true
        else if (D := PtDigit[C]):
            set Digits += 1
            if (InFrac?):
                set Scale = Scale / 10.0
                set Frac += Scale * (D * 1.0)
            else:
                set Whole = Whole * 10.0 + (D * 1.0)
        else:
            set Bad = true
    Digits > 0
    not Bad?
    Value := Whole + Frac
    if (Neg?) then -Value else Value

# Play Tester beacon: a few times a second, prints where each player stands, which way they
# look and how they are moving, so Ducky's Play Tester can steer by world coordinates (a
# target behind the player, behind a building or on another floor). It also prints the
# props you list under Landmarks, so Play Tester knows the layout. Place one in the island.
# Lines (centimetres, degrees):
#   [PTPOS] <tick> <player> <x> <y> <z> <yaw> <pitch> <ground|air|falling|gliding|water>
#   [PTMARK] <name> <x> <y> <z>
# Print "[PTEVENT] <what happened>" from your own devices to let a play test pass on it.
playtester_beacon_device := class(creative_device):

    # Seconds between position lines (0.2 to 1.0 works well).
    @editable
    Interval : float = 0.25

    # Props Play Tester should know about (doors, buttons, chests...), printed every 2 s.
    @editable
    Landmarks : []creative_prop = array{}

    # A name for each landmark, in the same order (empty: "landmark 1", "landmark 2"...).
    @editable
    LandmarkNames : []string = array{}

    OnBegin<override>()<suspends> : void =
        var Tick : int = 0
        var SinceMarks : float = 2.0
        loop:
            Sleep(Interval)
            set Tick += 1
            for (Index -> Player : GetPlayspace().GetPlayers(), Character := Player.GetFortCharacter[]):
                Location := Character.GetTransform().Translation
                Angles := Character.GetViewRotation().GetYawPitchRollDegrees()
                State := MovementOf(Character)
                if (Yaw := Angles[0], Pitch := Angles[1]):
                    Print("[PTPOS] {Tick} {Index} {Location.X} {Location.Y} {Location.Z} {Yaw} {Pitch} {State}", ?Duration := 0.0)
            set SinceMarks += Interval
            if (SinceMarks >= 2.0):
                set SinceMarks = 0.0
                for (Index -> Prop : Landmarks):
                    if (Prop.IsValid[]):
                        var Name : string = "landmark {Index + 1}"
                        if (Given := LandmarkNames[Index]):
                            set Name = Given
                        Where := Prop.GetTransform().Translation
                        Print("[PTMARK] {Name} {Where.X} {Where.Y} {Where.Z}", ?Duration := 0.0)

    # ground, air, falling, gliding or water (the last check that holds wins).
    MovementOf(Character : fort_character)<transacts> : string =
        var State : string = "air"
        if (Character.IsOnGround[]):
            set State = "ground"
        if (Character.IsFalling[]):
            set State = "falling"
        if (Character.IsGliding[]):
            set State = "gliding"
        if (Character.IsInWater[]):
            set State = "water"
        State
