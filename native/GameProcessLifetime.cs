// Session-local ownership only. No caller-supplied PID, path, key or coordinates.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.ComponentModel;
using System.Text;
using System.Threading;

internal sealed class GameProcessRecord {
    public int pid;
    public long born;
    public string path;
    public int parent;
}

internal sealed class GameOwnershipProbe {
    public GameProcessRecord game = null;
    public GameProcessRecord launcher = null;
    public long requested = 0;
    public Dictionary<string,long> baseline = null;
    public List<GameProcessRecord> ancestors = null;
}

internal sealed class GameProcessLifetime {
    const string Game = @"E:\Naraka\program\NarakaBladepoint.exe";
    const string Launcher = @"E:\Naraka\tool\LootHoarder.exe";
    readonly Dictionary<int,long> initial = new Dictionary<int,long>();
    readonly Dictionary<int,GameProcessRecord> owned = new Dictionary<int,GameProcessRecord>();
    readonly List<string> errors = new List<string>();
    readonly HashSet<int> unknown = new HashSet<int>();
    GameProcessRecord launcher;
    long requested;
    long attempt;
    bool retained;
    bool finished;
    object report;

    [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)] struct Entry {
        public uint size,usage,pid;
        public UIntPtr heap;
        public uint module,threads,parent;
        public int priority;
        public uint flags;
        [MarshalAs(UnmanagedType.ByValTStr,SizeConst=260)] public string name;
    }
    [DllImport("kernel32.dll")] static extern IntPtr CreateToolhelp32Snapshot(uint flags,uint pid);
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode)] static extern bool Process32First(IntPtr snapshot,ref Entry entry);
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode)] static extern bool Process32Next(IntPtr snapshot,ref Entry entry);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll",SetLastError=true)] static extern IntPtr OpenProcess(uint access,bool inherit,int pid);
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool QueryFullProcessImageName(IntPtr process,int flags,StringBuilder name,ref int count);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetProcessTimes(IntPtr process,out long born,out long ended,out long kernel,out long user);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool TerminateProcess(IntPtr process,uint code);
    delegate bool EnumProc(IntPtr window,IntPtr state);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc callback,IntPtr state);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window,out uint pid);
    [DllImport("user32.dll")] static extern bool PostMessage(IntPtr window,uint message,IntPtr a,IntPtr b);

    static Dictionary<int,int> Parents() {
        var result = new Dictionary<int,int>();
        IntPtr snapshot = CreateToolhelp32Snapshot(2,0);
        if(snapshot==new IntPtr(-1)) throw new InvalidOperationException("Cannot enumerate process ancestry");
        try {
            var entry = new Entry {size=(uint)Marshal.SizeOf(typeof(Entry))};
            if(Process32First(snapshot,ref entry)) do { result[(int)entry.pid]=(int)entry.parent; } while(Process32Next(snapshot,ref entry));
        } finally { CloseHandle(snapshot); }
        return result;
    }
    static GameProcessRecord Read(int pid,Dictionary<int,int> parents) {
        IntPtr process=OpenProcess(0x1000,false,pid); // QUERY_LIMITED_INFORMATION only.
        if(process==IntPtr.Zero) {
            int error=Marshal.GetLastWin32Error();
            if(error==87) throw new ArgumentException("Process exited");
            throw new Win32Exception(error);
        }
        try {
            int parent; parents.TryGetValue(pid,out parent);
            long born,ended,kernel,user;var name=new StringBuilder(32768);int count=name.Capacity;
            if(!GetProcessTimes(process,out born,out ended,out kernel,out user)
                || !QueryFullProcessImageName(process,0,name,ref count)) throw new Win32Exception(Marshal.GetLastWin32Error());
            return new GameProcessRecord {pid=pid,born=DateTime.FromFileTimeUtc(born).Ticks,path=name.ToString(),parent=parent};
        } finally {CloseHandle(process);}
    }
    static bool Same(GameProcessRecord record) {
        try {
            var current=Read(record.pid,new Dictionary<int,int>());
            return current.born==record.born && String.Equals(current.path,record.path,StringComparison.OrdinalIgnoreCase);
        } catch(ArgumentException) { return false; }
    }
    static void TerminateOwned(GameProcessRecord row) {
        // Verify and terminate through the SAME handle; a reused PID cannot
        // replace the process between the identity check and termination.
        IntPtr process=OpenProcess(0x1001,false,row.pid);
        if(process==IntPtr.Zero) {
            int error=Marshal.GetLastWin32Error();
            if(error==87) return;
            throw new Win32Exception(error);
        }
        try {
            long born,ended,kernel,user;var path=new StringBuilder(32768);int count=path.Capacity;
            if(!GetProcessTimes(process,out born,out ended,out kernel,out user)
                || !QueryFullProcessImageName(process,0,path,ref count)) throw new Win32Exception(Marshal.GetLastWin32Error());
            if(DateTime.FromFileTimeUtc(born).Ticks!=row.born || !String.Equals(path.ToString(),row.path,StringComparison.OrdinalIgnoreCase)) return;
            if(!TerminateProcess(process,0)) throw new Win32Exception(Marshal.GetLastWin32Error());
        } finally {CloseHandle(process);}
    }
    public static bool CanClaim(GameProcessRecord game,GameProcessRecord start,long at,
                                Dictionary<int,long> baseline,List<GameProcessRecord> ancestors) {
        if(start==null || at<=0 || game.born<at || !String.Equals(game.path,Game,StringComparison.OrdinalIgnoreCase)
            || !String.Equals(start.path,Launcher,StringComparison.OrdinalIgnoreCase)) return false;
        long original;
        if(baseline.TryGetValue(game.pid,out original) && (original==0 || original==game.born)) return false;
        int expected=game.parent;
        var seen=new HashSet<int>();
        foreach(var parent in ancestors) {
            if(parent.pid!=expected || !seen.Add(parent.pid) || parent.born>game.born) return false;
            if(parent.pid==start.pid) return parent.born==start.born
                && String.Equals(parent.path,start.path,StringComparison.OrdinalIgnoreCase);
            expected=parent.parent;
        }
        return false;
    }
    public GameProcessLifetime() {
        var parents=Parents();
        foreach(var process in Process.GetProcessesByName("NarakaBladepoint")) {
            using(process) {
                try { initial[process.Id]=Read(process.Id,parents).born; }
                catch { initial[process.Id]=0; }
            }
        }
    }
    public void PrepareLaunch(int launcherPid,long beforeClick) {
        if(requested>0) throw new InvalidOperationException("Game launch already requested");
        launcher=Read(launcherPid,Parents());
        if(!String.Equals(launcher.path,Launcher,StringComparison.OrdinalIgnoreCase)) throw new InvalidOperationException("Unexpected launcher");
        attempt=beforeClick;
    }
    public void Launched() {
        requested=attempt;
        Observe();
    }
    public void Observe() {
        if(launcher==null || requested<=0 || finished) return;
        var parents=Parents();
        foreach(var process in Process.GetProcessesByName("NarakaBladepoint")) {
            using(process) {
                try {
                    var game=Read(process.Id,parents);
                    unknown.Remove(game.pid);
                    if(owned.ContainsKey(game.pid)) continue;
                    var ancestors=new List<GameProcessRecord>();
                    int parent=game.parent;
                    for(int depth=0;parent>0 && depth<8;depth++) {
                        var item=Read(parent,parents); ancestors.Add(item);
                        if(parent==launcher.pid) break;
                        parent=item.parent;
                    }
                    if(Same(launcher) && CanClaim(game,launcher,requested,initial,ancestors)) owned[game.pid]=game;
                    else if(game.born>=requested && !initial.ContainsKey(game.pid)) unknown.Add(game.pid);
                } catch(ArgumentException) { /* Exited during observation. */ }
                catch(Exception e) { if(!initial.ContainsKey(process.Id)) {unknown.Add(process.Id);errors.Add("observe: "+e.GetType().Name);} }
            }
        }
        // Capture children while the exact root is alive, including before the
        // normal lobby exit. A later reparenting does not erase this evidence.
        bool added;
        do {
            added=false;
            foreach(var pair in parents) {
                if(owned.ContainsKey(pair.Key) || !owned.ContainsKey(pair.Value)) continue;
                try {
                    var parent=owned[pair.Value];
                    if(!Same(parent)) continue;
                    var child=Read(pair.Key,parents);
                    if(child.born>=parent.born) {owned[child.pid]=child;added=true;}
                } catch(ArgumentException) {} catch(Exception e) {errors.Add("child: "+e.GetType().Name);}
            }
        } while(added);
    }
    List<GameProcessRecord> Remaining() {
        var result=new List<GameProcessRecord>();
        foreach(var row in owned.Values) {
            try {if(Same(row)) result.Add(row);}
            catch(Exception e) {result.Add(row);errors.Add("verify: "+e.GetType().Name);}
        }
        return result;
    }
    void WaitUntilGone(int milliseconds) {
        var watch=Stopwatch.StartNew();
        while(watch.ElapsedMilliseconds<milliseconds && Remaining().Count>0) Thread.Sleep(100);
    }
    public object Finish(bool keep) {
        if(finished) return report;
        retained=keep;
        try {
            Observe();
            // A cancel can arrive between the accepted launcher click and the
            // game's first window. Observe a bounded startup gap before cleanup.
            if(!keep && requested>0 && owned.Count==0) {
                var watch=Stopwatch.StartNew();
                while(watch.ElapsedMilliseconds<15000 && owned.Count==0) {Thread.Sleep(250);Observe();}
            }
            if(!keep) {
                EnumWindows(delegate(IntPtr window,IntPtr unused) {
                    uint pid;GetWindowThreadProcessId(window,out pid);
                    GameProcessRecord row;
                    if(owned.TryGetValue((int)pid,out row)) {
                        try {if(Same(row)) PostMessage(window,0x10,IntPtr.Zero,IntPtr.Zero);} catch(Exception e) {errors.Add("close: "+e.GetType().Name);}
                    }
                    return true;
                },IntPtr.Zero);
                WaitUntilGone(5000);
                var targets=Remaining(); targets.Sort(delegate(GameProcessRecord a,GameProcessRecord b){return b.born.CompareTo(a.born);});
                foreach(var row in targets) {
                    try {TerminateOwned(row);}
                    catch(ArgumentException) {} catch(Exception e) {errors.Add("terminate: "+e.GetType().Name);}
                }
                WaitUntilGone(3000);
            }
        } catch(Exception e) {errors.Add("cleanup: "+e.GetType().Name);}
        finished=true;
        report=new {ok=true,cleanup_report=true,retained=retained,owned=new List<GameProcessRecord>(owned.Values),
                    remaining=Remaining(),unverified_new_pids=new List<int>(unknown),errors=errors,input_sent=false};
        return report;
    }
}
