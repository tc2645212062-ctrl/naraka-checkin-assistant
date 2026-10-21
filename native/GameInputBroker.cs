// A bounded, per-user broker. No caller-supplied paths, keys or coordinates.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.IO.Pipes;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

class GameInputBroker {
    const string Game = @"E:\Naraka\program\NarakaBladepoint.exe";
    const string Launcher = @"E:\Naraka\tool\LootHoarder.exe";
    static JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 24000000 };
    static HashSet<string> Verified = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
    static DateTime Deadline;
    static int Actions;
    [StructLayout(LayoutKind.Sequential)] struct Rect { public int L,T,R,B; }
    [StructLayout(LayoutKind.Sequential)] struct MouseInput { public int X,Y; public uint Data,Flags,Time; public UIntPtr Extra; }
    [StructLayout(LayoutKind.Sequential)] struct KeyboardInput { public ushort Key,Scan; public uint Flags,Time; public UIntPtr Extra; }
    [StructLayout(LayoutKind.Explicit)] struct InputData { [FieldOffset(0)] public MouseInput Mouse; [FieldOffset(0)] public KeyboardInput Keyboard; }
    [StructLayout(LayoutKind.Sequential)] struct Input { public uint Type; public InputData Data; }
    delegate bool EnumProc(IntPtr window, IntPtr state);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc proc,IntPtr arg);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr w,StringBuilder name,int count);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr w);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr w,out uint pid);
    [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
    [DllImport("kernel32.dll")] static extern IntPtr OpenProcess(uint access,bool inherit,uint pid);
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode)] static extern bool QueryFullProcessImageName(IntPtr p,int flags,StringBuilder name,ref int length);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr p);
    [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr w,out Rect r);
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr w);
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr w,int command);
    [DllImport("user32.dll")] static extern bool IsIconic(IntPtr w);
    [DllImport("user32.dll")] static extern bool AttachThreadInput(uint a,uint b,bool attach);
    [DllImport("user32.dll")] static extern bool SetCursorPos(int x,int y);
    [DllImport("user32.dll")] static extern IntPtr WindowFromPoint(Point p);
    [DllImport("user32.dll")] static extern IntPtr GetAncestor(IntPtr w,uint flag);
    [DllImport("user32.dll")] static extern uint SendInput(uint n,Input[] input,int size);
    [DllImport("user32.dll")] static extern bool SetProcessDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll")] static extern IntPtr OpenInputDesktop(uint flags,bool inherit,uint access);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern bool GetUserObjectInformation(IntPtr h,int index,StringBuilder info,int length,out int needed);
    [DllImport("user32.dll")] static extern bool CloseDesktop(IntPtr h);
    [DllImport("user32.dll")] static extern bool PostMessage(IntPtr w,uint message,IntPtr a,IntPtr b);

    static void Guard() {
        if (DateTime.UtcNow > Deadline || Actions > 16) throw new Exception("timeout: bounded session ended");
        IntPtr desktop=OpenInputDesktop(0,false,1);
        if(desktop==IntPtr.Zero) throw new Exception("desktop_busy: input desktop unavailable");
        try { int n; var name=new StringBuilder(256); if(!GetUserObjectInformation(desktop,2,name,512,out n) || name.ToString()!="Default") throw new Exception("desktop_busy: secure or locked desktop"); }
        finally { CloseDesktop(desktop); }
    }
    static string PathOf(IntPtr w) {
        uint pid; GetWindowThreadProcessId(w,out pid);
        IntPtr p=OpenProcess(0x1000,false,pid); if(p==IntPtr.Zero) return "";
        try { int n=32768; var b=new StringBuilder(n); return QueryFullProcessImageName(p,0,b,ref n)?b.ToString():""; }
        finally { CloseHandle(p); }
    }
    static void VerifyPublisher(string path) {
        if(Verified.Contains(path)) return;
        // Both paths are compile-time constants, never IPC values. This command
        // only checks Authenticode; it cannot install or execute the target.
        if(path!=Game && path!=Launcher) throw new Exception("Unexpected executable");
        var ps=new ProcessStartInfo(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),@"WindowsPowerShell\v1.0\powershell.exe"));
        string script="$s=Get-AuthenticodeSignature -LiteralPath '"+path+"'; if($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notlike '*NetEase*Hangzhou*Network*'){exit 1}; exit 0";
        ps.Arguments="-NoProfile -NonInteractive -EncodedCommand "+Convert.ToBase64String(Encoding.Unicode.GetBytes(script));
        ps.UseShellExecute=false; ps.CreateNoWindow=true;
        ps.EnvironmentVariables["PSModulePath"]=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),@"WindowsPowerShell\v1.0\Modules");
        using(var p=Process.Start(ps)) { if(!p.WaitForExit(15000)) { p.Kill(); throw new Exception("Signature verification timeout"); } if(p.ExitCode!=0) throw new Exception("Official game signature invalid"); }
        Verified.Add(path);
    }
    static IntPtr SelectWindow(out string kind) {
        Guard(); var games=new List<IntPtr>(); var launchers=new List<IntPtr>();
        EnumWindows(delegate(IntPtr w,IntPtr unused) {
            if(!IsWindowVisible(w)) return true;
            string path=PathOf(w); if(path==Game && GetAncestor(w,3)==w && ClassOf(w)=="UnityWndClass") games.Add(w); else if(path==Launcher) { Rect r; GetWindowRect(w,out r); if(IsIconic(w) || (r.R-r.L>700 && r.B-r.T>400)) launchers.Add(w); }
            return true;
        },IntPtr.Zero);
        var candidates=games.Count>0?games:launchers;
        kind=games.Count>0?"game":"launcher";
        if(candidates.Count!=1) throw new Exception(candidates.Count==0?"entry_unavailable: no game window":"unrecognized: multiple game windows");
        VerifyPublisher(kind=="game"?Game:Launcher);
        return candidates[0];
    }
    static string ClassOf(IntPtr w) {
        var name=new StringBuilder(256); GetClassName(w,name,name.Capacity); return name.ToString();
    }
    static bool IsOwnedLogin(IntPtr w,IntPtr game) {
        if(w==IntPtr.Zero || w==game || PathOf(w)!=Game || ClassOf(w)!="MPAY_LOGIN") return false;
        uint loginPid,gamePid; GetWindowThreadProcessId(w,out loginPid); GetWindowThreadProcessId(game,out gamePid);
        Rect dialog,main; GetWindowRect(w,out dialog); GetWindowRect(game,out main);
        // NetEase removes the native owner after showing the MPAY window.
        // Require the exact game PID/class and containment instead.
        return loginPid==gamePid && dialog.L>=main.L && dialog.T>=main.T && dialog.R<=main.R && dialog.B<=main.B;
    }
    static bool HasFocus(IntPtr w) {
        IntPtr current=GetForegroundWindow(); return current==w || IsOwnedLogin(current,w);
    }
    static void Focus(IntPtr w) {
        Guard(); if(HasFocus(w)) return; if(IsIconic(w)) ShowWindow(w,9);
        SetForegroundWindow(w);
        if(!HasFocus(w)) {
            try {
                var type=Type.GetType("System.Windows.Automation.AutomationElement, UIAutomationClient, Version=4.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35",true);
                var element=type.GetMethod("FromHandle").Invoke(null,new object[]{w});
                type.GetMethod("SetFocus").Invoke(element,null);
            } catch { /* Fall back to the normal window focus API below. */ }
        }
        if(!HasFocus(w)) {
            uint pid; uint thread=GetWindowThreadProcessId(GetForegroundWindow(),out pid); uint own=GetCurrentThreadId();
            bool attached=thread!=own && AttachThreadInput(own,thread,true);
            try { SetForegroundWindow(w); } finally { if(attached) AttachThreadInput(own,thread,false); }
        }
        Thread.Sleep(150);
        if(!HasFocus(w)) throw new Exception("desktop_busy: cannot focus game");
    }
    static Bitmap Capture(IntPtr w) {
        Guard(); Focus(w); Rect r; GetWindowRect(w,out r);
        if(r.R-r.L<700 || r.B-r.T<400 || r.R-r.L>8000 || r.B-r.T>5000) throw new Exception("unrecognized: window dimensions");
        var image=new Bitmap(r.R-r.L,r.B-r.T,PixelFormat.Format24bppRgb);
        using(var g=Graphics.FromImage(image)) g.CopyFromScreen(r.L,r.T,0,0,image.Size);
        return image;
    }
    static byte[] Png(Bitmap image) { using(var s=new MemoryStream()) { image.Save(s,ImageFormat.Png); return s.ToArray(); } }
    static Rectangle Patch(string action,int width,int height) {
        // Templates contain only fixed control labels, no account identifiers.
        int x,y,w,h,bw=2560,bh=1600;
        switch(action) {
            case "launcher_start": x=1237;y=788;w=137;h=52;bw=1538;bh=932;break;
            case "launcher_start_current": x=1279;y=788;w=137;h=52;bw=1538;bh=932;break;
            case "announcement": x=1283;y=1224;w=88;h=45;break;
            case "saved_account": x=1225;y=886;w=112;h=39;break;
            case "title_enter": x=1155;y=1343;w=251;h=40;break;
            case "dismiss_monthly": x=1860;y=525;w=205;h=63;break;
            case "exit_lobby": x=1188;y=22;w=82;h=46;break;
            case "lobby_nav": x=904;y=23;w=260;h=39;break;
            case "lobby_nav_current": x=904;y=23;w=260;h=39;break;
            case "lobby_identity": x=1100;y=23;w=77;h=39;break;
            case "dismiss_promotion": x=2395;y=1548;w=140;h=42;break;
            case "promotion_notice": x=2205;y=1545;w=180;h=48;break;
            default: throw new Exception("Unsupported action");
        }
        return new Rectangle((int)Math.Round(x*(double)width/bw),(int)Math.Round(y*(double)height/bh),Math.Max(1,(int)Math.Round(w*(double)width/bw)),Math.Max(1,(int)Math.Round(h*(double)height/bh)));
    }
    static double Match(Bitmap frame,string action) {
        Rectangle matched; return Match(frame,action,out matched);
    }
    static double Match(Bitmap frame,string action,out Rectangle matched) {
        double best=MatchSingle(frame,action,out matched);
        if(action=="launcher_start") {
            Rectangle current; double alternative=MatchSingle(frame,"launcher_start_current",out current);
            if(alternative>best) { best=alternative;matched=current; }
        }
        return best;
    }
    static double MatchSingle(Bitmap frame,string action,out Rectangle matched) {
        // A restored border can translate a control by a few physical pixels.
        // Search only the neighbourhood of its fixed, scaled anchor.
        Rectangle anchor=Patch(action,frame.Width,frame.Height); matched=anchor;
        bool launcher=action=="launcher_start" || action=="launcher_start_current";
        int radius=Math.Max(2,(int)Math.Ceiling(4.0*frame.Width/(launcher?1538:2560)));
        double best=-1;
        using(var stream=Assembly.GetExecutingAssembly().GetManifestResourceStream(action+".png"))
        using(var template=new Bitmap(stream))
        using(var crop=new Bitmap(template.Width,template.Height)) {
            int n=template.Width*template.Height; var values=new double[n];
            double b=0,bb=0;
            for(int y=0,i=0;y<template.Height;y++) for(int x=0;x<template.Width;x++,i++) {
                Color q=template.GetPixel(x,y); double v=(q.R+q.G+q.B)/3.0;
                values[i]=v;b+=v;bb+=v*v;
            }
            for(int dy=-radius;dy<=radius;dy++) for(int dx=-radius;dx<=radius;dx++) {
                var candidate=new Rectangle(anchor.X+dx,anchor.Y+dy,anchor.Width,anchor.Height);
                if(candidate.Left<0 || candidate.Top<0 || candidate.Right>frame.Width || candidate.Bottom>frame.Height) continue;
                using(var g=Graphics.FromImage(crop)) g.DrawImage(frame,new Rectangle(0,0,crop.Width,crop.Height),candidate,GraphicsUnit.Pixel);
                double a=0,aa=0,ab=0;
                for(int y=0,i=0;y<crop.Height;y++) for(int x=0;x<crop.Width;x++,i++) {
                    Color p=crop.GetPixel(x,y); double u=(p.R+p.G+p.B)/3.0;
                    a+=u;aa+=u*u;ab+=u*values[i];
                }
                double denom=Math.Sqrt(Math.Max(0,aa-a*a/n)*Math.Max(0,bb-b*b/n));
                double score=denom<1?0:(ab-a*b/n)/denom;
                if(score>best) { best=score;matched=candidate; }
            }
        }
        return best;
    }
    static void Key(IntPtr w,ushort scan) {
        Guard(); if(GetForegroundWindow()!=w) throw new Exception("desktop_busy: focus changed");
        var down=new Input { Type=1,Data=new InputData { Keyboard=new KeyboardInput { Scan=scan,Flags=8 } } };
        var up=down;up.Data.Keyboard.Flags=10;
        if(SendInput(1,new[]{down},Marshal.SizeOf(typeof(Input)))!=1) throw new Exception("Input rejected");
        try { Thread.Sleep(90); } finally { SendInput(1,new[]{up},Marshal.SizeOf(typeof(Input))); }
    }
    static void Click(IntPtr w,int x,int y,bool savedAccount) {
        Guard(); Rect r; GetWindowRect(w,out r); var p=new Point(r.L+x,r.T+y);
        IntPtr hit=GetAncestor(WindowFromPoint(p),2);
        bool owned=savedAccount && IsOwnedLogin(hit,w) && HasFocus(w);
        if(!owned && (GetForegroundWindow()!=w || hit!=w)) throw new Exception("desktop_busy: control covered");
        if(!SetCursorPos(p.X,p.Y)) throw new Exception("Input rejected: cursor position");
        var down=new Input { Type=0,Data=new InputData { Mouse=new MouseInput { Flags=2 } } };
        var up=down;up.Data.Mouse.Flags=4;
        if(SendInput(1,new[]{down},Marshal.SizeOf(typeof(Input)))!=1) throw new Exception("Input rejected");
        try { Thread.Sleep(90); } finally { SendInput(1,new[]{up},Marshal.SizeOf(typeof(Input))); }
    }
    static object Handle(string command) {
        string kind; IntPtr w=SelectWindow(out kind);
        using(var image=Capture(w)) {
            if(command=="observe") return new {ok=true,kind=kind,width=image.Width,height=image.Height,png=Convert.ToBase64String(Png(image))};
            if(!command.StartsWith("act:")) throw new Exception("Unsupported command");
            string action=command.Substring(4);
            if(action!="launcher_start" && action!="announcement" && action!="saved_account" && action!="title_enter" && action!="dismiss_monthly" && action!="dismiss_promotion" && action!="exit_lobby") throw new Exception("Unsupported action");
            if((action=="launcher_start")!=(kind=="launcher")) throw new Exception("Wrong target process");
            Rectangle matched; double score;
            string rejection=RejectionReason(image,action,out matched,out score);
            if(rejection!=null) return Rejected(image,kind,action,rejection,score);
            Actions++;
            if(action=="exit_lobby") {
                Guard(); if(GetForegroundWindow()!=w || PathOf(w)!=Game) throw new Exception("desktop_busy: game changed");
                if(!PostMessage(w,0x10,IntPtr.Zero,IntPtr.Zero)) throw new Exception("Game close request rejected");
            }
            else if(action=="announcement") Key(w,0x39);
            else if(action=="dismiss_monthly" || action=="dismiss_promotion") Key(w,0x01);
            else { Click(w,matched.X+matched.Width/2,matched.Y+matched.Height/2,action=="saved_account"); }
            return new {ok=true,action=action,match=score};
        }
    }
    static string RejectionReason(Bitmap image,string action,out Rectangle matched,out double score) {
        score=Match(image,action,out matched);
        if(score<0.78) return "control template mismatch";
        if(action=="dismiss_promotion" && Match(image,"promotion_notice")<0.78) return "promotion notice missing";
        // Menu items to the left change between game modes. The adjacent
        // guild label stays at the same anchor in the observed lobbies.
        if(action=="exit_lobby" && Match(image,"lobby_identity")<0.78) return "lobby identity missing";
        if(action=="exit_lobby" && Match(image,"promotion_notice")>=0.78) return "promotion still covers lobby";
        return null;
    }
    static object Rejected(Bitmap image,string kind,string action,string reason,double score) {
        return new {ok=false,error="unrecognized: "+reason+" ("+score.ToString("F3")+")",
            code="control_not_ready",action=action,kind=kind,input_sent=false,match=score,
            identity_match=action=="exit_lobby"?Match(image,"lobby_identity"):0,
            width=image.Width,height=image.Height,png=Convert.ToBase64String(Png(image))};
    }
    static string ReadCommand(StreamReader reader) {
        var value=new StringBuilder(); var ch=new char[1]; DateTime until=DateTime.UtcNow.AddSeconds(40);
        while(value.Length<=40 && DateTime.UtcNow<Deadline && DateTime.UtcNow<until) {
            var next=reader.ReadAsync(ch,0,1);
            TimeSpan wait=until-DateTime.UtcNow;
            if(wait<=TimeSpan.Zero || !next.Wait(wait) || next.Result==0) return null;
            if(ch[0]=='\n') return value.ToString();
            if(ch[0]!='\r') value.Append(ch[0]);
        }
        return null;
    }
    static int Main(string[] args) { try { return Run(args); } catch(Exception e) { Console.Error.WriteLine(e.Message); return 1; } }
    static int Run(string[] args) {
        if(args.Length>0) {
            if(args.Length==1 && args[0]=="--version") { Console.WriteLine("4"); return 0; }
            if(args.Length==1 && args[0]=="--focus-test") { SetProcessDpiAwarenessContext(new IntPtr(-4)); Deadline=DateTime.UtcNow.AddMinutes(1); string kind; Focus(SelectWindow(out kind)); return 0; }
            // Offline template validation, no desktop input, installation or IPC.
            if(args.Length==3 && args[0]=="--match") { using(var b=new Bitmap(args[2])) Console.WriteLine(Match(b,args[1]).ToString("R",System.Globalization.CultureInfo.InvariantCulture)); return 0; }
            if(args.Length==2 && args[0]=="--check-launcher") {
                using(var b=new Bitmap(args[1])) {
                    Rectangle matched; double score; string rejection=RejectionReason(b,"launcher_start",out matched,out score);
                    Console.WriteLine(Json.Serialize(new {allowed=rejection==null,reason=rejection,match=score,
                        point_x=matched.X+matched.Width/2,point_y=matched.Y+matched.Height/2,input_sent=false}));
                }
                return 0;
            }
            if(args.Length==2 && args[0]=="--check-exit") {
                using(var b=new Bitmap(args[1])) {
                    Rectangle matched; double score; string rejection=RejectionReason(b,"exit_lobby",out matched,out score);
                    Console.WriteLine(Json.Serialize(new {allowed=rejection==null,reason=rejection,match=score,identity_match=Match(b,"lobby_identity"),input_sent=false}));
                }
                return 0;
            }
            return 2;
        }
        if(!new WindowsPrincipal(WindowsIdentity.GetCurrent()).IsInRole(WindowsBuiltInRole.Administrator)) return 3;
        SetProcessDpiAwarenessContext(new IntPtr(-4));
        string sid=WindowsIdentity.GetCurrent().User.Value;
        bool created;
        using(var mutex=new Mutex(true,"Local\\NarakaGameInputBroker-"+sid,out created)) {
            if(!created) return 4;
            Deadline=DateTime.UtcNow.AddMinutes(8);
            var security=new PipeSecurity();
            security.SetAccessRuleProtection(true,false);
            security.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(sid),PipeAccessRights.FullControl,AccessControlType.Allow));
            security.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.LocalSystemSid,null),PipeAccessRights.FullControl,AccessControlType.Allow));
            using(var pipe=new NamedPipeServerStream("NarakaGameInput-"+sid,PipeDirection.InOut,1,PipeTransmissionMode.Byte,PipeOptions.Asynchronous,4096,4096,security)) {
                var connect=pipe.BeginWaitForConnection(null,null);
                if(!connect.AsyncWaitHandle.WaitOne(TimeSpan.FromSeconds(45))) return 5;
                pipe.EndWaitForConnection(connect);
                using(var reader=new StreamReader(pipe,new UTF8Encoding(false)))
                using(var writer=new StreamWriter(pipe,new UTF8Encoding(false)) {AutoFlush=true}) {
                    while(DateTime.UtcNow<Deadline) {
                        string command=ReadCommand(reader);
                        if(command==null || command=="stop") break;
                        if(command.Length>40) break;
                        object result;
                        try { result=Handle(command); } catch(Exception e) { result=new {ok=false,error=e.Message}; }
                        writer.WriteLine(Json.Serialize(result));
                    }
                }
            }
        }
        return 0;
    }
}
