using { /Fortnite.com/Devices }
using { /Fortnite.com/Characters }
using { /Verse.org/Simulation }
using { /UnrealEngine.com/Temporary/SpatialMath }

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
