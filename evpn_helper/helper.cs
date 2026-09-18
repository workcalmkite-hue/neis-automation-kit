// EVPN 도우미 — AXGATE VPN Client 의 네이티브 창을 선생님 대신 처리한다.
//
// 왜 따로 있나:
//   AXGATE 클라이언트는 관리자 권한(requireAdministrator)으로 돈다. 윈도우는 일반 권한 프로그램이
//   관리자 권한 창에 글자를 넣거나 버튼을 누르는 것을 조용히 막는다(UIPI). 그래서 2차 인증 창에
//   인증서 암호를 넣으려면 이 도우미도 관리자 권한이어야 한다.
//   연결할 때마다 UAC [예] 가 뜨지 않도록, 설치 때 한 번만 [예] 를 누르고
//   «가장 높은 권한으로 실행» 예약 작업으로 등록해 둔다 (install.ps1).
//
// 하는 일 (한 번 실행에 최대 300초):
//   1) AXGATE 클라이언트가 안 떠 있으면 띄운다 — 설치 때 기록한 파일 목록·해시와 같을 때만
//   2) «2차 인증» 창: 설정의 인증서 이름과 맞는 줄 하나를 골라 암호를 넣고 [로그인] (최대 2번)
//   3) «이미 접속되어 있는 ID 입니다» 창: [예] (최대 1번)
//   4) 2차 인증 뒤 «로그인 후 자동 실행» 창을 닫는다
//   5) 키트가 끝 신호 파일을 만들거나 300초가 지나면 끝낸다
//
// ⚠️ 이 파일은 설치 때 C:\Program Files\NeisAutomationEVPN 에서 컴파일된다. 고쳤으면
//    python evpn.py install 을 다시 해야 반영된다.
// ⚠️ 로그에 암호·인증서 이름을 절대 쓰지 않는다.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

static class Helper
{
    const string AXGATE_BIN = @"C:\ProgramData\AXGATE\AXGATE VPN Client\Bin";
    const string AXGATE_EXE = AXGATE_BIN + @"\AxgateVpnClient.exe";
    const string KEYRING_SERVICE = "neis-automation";
    const int MAX_SECONDS = 300;
    const int MAX_2FA = 2;          // 포털 로그인 2번까지 (키트의 1회 재시도)
    const int MAX_DUP = 1;          // «이미 접속» 교체는 1번만

    static string BaseDir = AppDomain.CurrentDomain.BaseDirectory;
    static string LogPath = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),
                                          "NeisAutomationEVPN", "helper.log");
    static string UserDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                                          "neis-automation");
    static string DoneFlag = Path.Combine(UserDir, "evpn_helper_done.flag");

    static void Log(string m)
    {
        try { File.AppendAllText(LogPath, DateTime.Now.ToString("HH:mm:ss") + "  " + m + "\r\n", Encoding.UTF8); }
        catch { }
    }

    [STAThread]
    static int Main(string[] args)
    {
        bool created;
        using (var mtx = new Mutex(true, @"Global\NeisAutomationEvpnHelper", out created))
        {
            if (!created) return 0;     // 이미 도는 중
            try { File.WriteAllText(LogPath, "", Encoding.UTF8); } catch { }
            bool admin = new WindowsPrincipal(WindowsIdentity.GetCurrent()).IsInRole(WindowsBuiltInRole.Administrator);
            Log("[시작] 관리자=" + admin + " 사용자=" + Environment.UserName);
            if (!admin)
                Log("[경고] 관리자 권한이 아닙니다 — AXGATE 창을 누를 수 없습니다 (표준 사용자 계정일 수 있음)");
            DateTime started = DateTime.Now;
            try { Run(started); }
            catch (Exception e) { Log("[오류] " + e.GetType().Name + ": " + e.Message); return 1; }
            Log("[끝]");
        }
        return 0;
    }

    static void Run(DateTime started)
    {
        EnsureAxgate();

        string certName = ReadCertName();
        string pw = certName == null ? null : ReadPassword(certName);
        if (certName == null) Log("[설정] 인증서 이름이 설정에 없습니다 — 2차 인증을 못 넣습니다");
        else if (pw == null) Log("[설정] 저장된 인증서 암호가 없습니다 — 2차 인증을 못 넣습니다");

        var seen = new HashSet<IntPtr>();
        int done2fa = 0, dup = 0;
        DateTime last2fa = DateTime.MinValue;
        while ((DateTime.Now - started).TotalSeconds < MAX_SECONDS)
        {
            if (File.Exists(DoneFlag) && File.GetLastWriteTime(DoneFlag) > started) { Log("[신호] 키트가 끝 신호를 보냄"); break; }

            IntPtr dlg = FindDialogWithChild("2차 인증");
            if (dlg != IntPtr.Zero && !seen.Contains(dlg))
            {
                seen.Add(dlg);     // 처리 전에 먼저 표시 — 같은 창을 두 번 누르지 않는다
                if (done2fa >= MAX_2FA) Log("[2차인증] 횟수 초과 — 이번 창은 건드리지 않습니다");
                else if (pw == null) Log("[2차인증] 창이 떴지만 암호가 없어 못 넣습니다");
                else if (Handle2fa(dlg, certName, pw)) { done2fa++; last2fa = DateTime.Now; }
            }

            IntPtr d2 = FindDialogWithChild("이미 접속되어 있는 ID");
            if (d2 != IntPtr.Zero && !seen.Contains(d2))
            {
                seen.Add(d2);
                if (dup >= MAX_DUP) Log("[이미접속] 두 번째 창 — 누르지 않습니다");
                else if (ClickButton(d2, "예")) { dup++; Log("[이미접속] 기존 접속 끊기 [예]"); }
            }

            if (done2fa > 0)
            {
                IntPtr ar = FindTopByTitle("로그인 후 자동 실행");
                if (ar != IntPtr.Zero) { PostMessage(ar, WM_CLOSE, IntPtr.Zero, IntPtr.Zero); Log("[자동실행창] 닫음"); }
            }
            Thread.Sleep(400);
        }
        Log("[요약] 2차인증 " + done2fa + "번 · 이미접속 " + dup + "번");
    }

    // ---------------------------------------------------------------- AXGATE 띄우기
    static void EnsureAxgate()
    {
        if (Process.GetProcessesByName("AxgateVpnClient").Length > 0) { Log("[AXGATE] 이미 실행 중"); return; }
        if (!File.Exists(AXGATE_EXE)) { Log("[AXGATE] 설치되어 있지 않습니다"); return; }
        string why = CheckBaseline();
        if (why != null)
        {
            // 관리자 권한으로 띄우는 것이라, 설치 뒤 바뀐 파일이 있으면 절대 띄우지 않는다.
            // (AXGATE 폴더는 일반 사용자도 새 파일을 넣을 수 있다 — 윈도우 ProgramData 기본 권한)
            Log("[AXGATE] 띄우지 않음 — " + why + " → python evpn.py install 을 다시 하면 기준이 갱신됩니다");
            return;
        }
        var psi = new ProcessStartInfo(AXGATE_EXE) { UseShellExecute = false, WorkingDirectory = AXGATE_BIN };
        Process.Start(psi);
        Log("[AXGATE] 실행함");
    }

    static string CheckBaseline()
    {
        string f = Path.Combine(BaseDir, "axgate_baseline.txt");
        if (!File.Exists(f)) return "기준 파일 없음";
        var want = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var line in File.ReadAllLines(f))
        {
            var p = line.Split('|');
            if (p.Length == 2) want[p[0]] = p[1];
        }
        var now = Directory.GetFiles(AXGATE_BIN, "*", SearchOption.AllDirectories)
            .Where(x => x.EndsWith(".exe", StringComparison.OrdinalIgnoreCase) || x.EndsWith(".dll", StringComparison.OrdinalIgnoreCase))
            .ToList();
        foreach (var path in now)
        {
            string rel = path.Substring(AXGATE_BIN.Length + 1);
            if (!want.ContainsKey(rel)) return "설치 때 없던 파일: " + rel;
            if (!string.Equals(Sha256(path), want[rel], StringComparison.OrdinalIgnoreCase)) return "바뀐 파일: " + rel;
        }
        if (now.Count != want.Count) return "파일 수가 다름 (기준 " + want.Count + " / 지금 " + now.Count + ")";
        return null;
    }

    static string Sha256(string path)
    {
        using (var s = File.OpenRead(path))
        using (var h = SHA256.Create())
            return BitConverter.ToString(h.ComputeHash(s)).Replace("-", "");
    }

    // ---------------------------------------------------------------- 설정·암호
    static string ReadCertName()
    {
        string f = Path.Combine(UserDir, "teacher_config.json");
        if (!File.Exists(f)) return null;
        var d = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(f, Encoding.UTF8));
        object v;
        return d.TryGetValue("cert_name", out v) && v is string && ((string)v).Trim().Length > 0 ? ((string)v).Trim() : null;
    }

    // 파이썬 keyring 이 윈도우 자격 증명 관리자에 넣은 값을 읽는다.
    // 대상 이름은 "<계정>@neis-automation" (같은 서비스에 계정이 둘 이상이면) 또는 "neis-automation".
    static string ReadPassword(string account)
    {
        foreach (var target in new[] { account + "@" + KEYRING_SERVICE, KEYRING_SERVICE })
        {
            IntPtr p;
            if (!CredRead(target, 1, 0, out p)) continue;
            try
            {
                var c = (CREDENTIAL)Marshal.PtrToStructure(p, typeof(CREDENTIAL));
                string user = c.UserName == null ? "" : Marshal.PtrToStringUni(c.UserName);
                if (target == KEYRING_SERVICE && user != account) continue;
                if (c.CredentialBlobSize == 0) continue;
                var b = new byte[c.CredentialBlobSize];
                Marshal.Copy(c.CredentialBlob, b, 0, b.Length);
                return Encoding.Unicode.GetString(b).TrimEnd('\0');
            }
            finally { CredFree(p); }
        }
        return null;
    }

    // ---------------------------------------------------------------- 2차 인증 창
    static bool Handle2fa(IntPtr dlg, string certName, string pw)
    {
        IntPtr hdd = IntPtr.Zero, list = IntPtr.Zero, edit = IntPtr.Zero, login = IntPtr.Zero;
        foreach (var c in Children(dlg))
        {
            string cl = ClassOf(c), tx = TextOf(c).Trim();
            if (cl == "Button" && tx == "하드디스크" && hdd == IntPtr.Zero) hdd = c;
            else if (cl == "Button" && tx == "로그인" && login == IntPtr.Zero) login = c;
            else if (cl == "SysListView32" && list == IntPtr.Zero) list = c;
            else if (cl == "Edit" && edit == IntPtr.Zero) edit = c;
        }
        if (list == IntPtr.Zero || edit == IntPtr.Zero || login == IntPtr.Zero)
        {
            Log("[2차인증] 창 안의 칸을 못 찾음 (목록=" + (list != IntPtr.Zero) + " 암호칸=" + (edit != IntPtr.Zero) + " 로그인=" + (login != IntPtr.Zero) + ")");
            return false;
        }
        if (hdd != IntPtr.Zero) { SendMessage(hdd, BM_CLICK, IntPtr.Zero, IntPtr.Zero); Thread.Sleep(600); }

        int count = (int)SendMessage(list, LVM_GETITEMCOUNT, IntPtr.Zero, IntPtr.Zero);
        if (count <= 0) { Log("[2차인증] 인증서 목록이 비어 있습니다 (C:\\GPKI 에 인증서가 있는지)"); return false; }

        // 인증서 이름이 들어 있는 줄을 찾는다. 딱 한 줄일 때만 고른다 — 여러 줄이면 멋대로 고르지 않는다.
        var rows = ReadListRows(list, count);
        int idx = -1;
        if (rows != null)
        {
            var hit = Enumerable.Range(0, count).Where(i => rows[i].Contains(certName)).ToList();
            if (hit.Count == 1) idx = hit[0];
            else { Log("[2차인증] 인증서 " + count + "개 중 이름이 맞는 것 " + hit.Count + "개 — 고르지 않고 멈춥니다"); return false; }
        }
        else if (count == 1) idx = 0;
        else { Log("[2차인증] 인증서가 " + count + "개인데 목록 글자를 못 읽어 고르지 않습니다"); return false; }

        if (!SelectRow(list, idx)) { Log("[2차인증] 인증서 줄 선택을 확인 못 함"); return false; }
        Log("[2차인증] 인증서 " + count + "개 중 " + (idx + 1) + "번째 선택");

        SendMessage(edit, WM_SETTEXT, IntPtr.Zero, pw);
        int n = (int)SendMessage(edit, WM_GETTEXTLENGTH, IntPtr.Zero, IntPtr.Zero);
        if (n != pw.Length)
        {
            SendMessage(edit, WM_SETTEXT, IntPtr.Zero, "");
            foreach (char ch in pw) { PostMessage(edit, WM_CHAR, (IntPtr)ch, IntPtr.Zero); Thread.Sleep(25); }
            Thread.Sleep(400);
            n = (int)SendMessage(edit, WM_GETTEXTLENGTH, IntPtr.Zero, IntPtr.Zero);
        }
        if (n != pw.Length) { Log("[2차인증] 암호 칸에 글자가 다 안 들어감 (" + n + "/" + pw.Length + ")"); return false; }
        SendMessage(login, BM_CLICK, IntPtr.Zero, IntPtr.Zero);
        Log("[2차인증] 암호 넣고 [로그인]");
        return true;
    }

    static bool SelectRow(IntPtr list, int idx)
    {
        PostMessage(list, WM_KEYDOWN, (IntPtr)VK_HOME, IntPtr.Zero); PostMessage(list, WM_KEYUP, (IntPtr)VK_HOME, IntPtr.Zero);
        Thread.Sleep(200);
        for (int i = 0; i < idx; i++)
        {
            PostMessage(list, WM_KEYDOWN, (IntPtr)VK_DOWN, IntPtr.Zero); PostMessage(list, WM_KEYUP, (IntPtr)VK_DOWN, IntPtr.Zero);
            Thread.Sleep(150);
        }
        for (int t = 0; t < 10; t++)
        {
            Thread.Sleep(150);
            if ((int)SendMessage(list, LVM_GETNEXTITEM, (IntPtr)(-1), (IntPtr)LVNI_SELECTED) == idx) return true;
        }
        return false;
    }

    // 다른 프로세스의 목록(SysListView32) 글자는 그 프로세스 메모리 안에서만 읽힌다.
    static string[] ReadListRows(IntPtr list, int count)
    {
        uint pid;
        GetWindowThreadProcessId(list, out pid);
        IntPtr hp = OpenProcess(0x0008 | 0x0010 | 0x0020 | 0x0400, false, pid);
        if (hp == IntPtr.Zero) return null;
        try
        {
            bool wow;
            IsWow64Process(hp, out wow);
            bool is32 = wow || IntPtr.Size == 4;
            IntPtr mem = VirtualAllocEx(hp, IntPtr.Zero, (IntPtr)4096, 0x3000, 0x04);
            if (mem == IntPtr.Zero) return null;
            try
            {
                var rows = new string[count];
                long textAddr = mem.ToInt64() + 1024;
                for (int i = 0; i < count; i++)
                {
                    var sb = new StringBuilder();
                    for (int sub = 0; sub < 5; sub++)
                    {
                        byte[] item = new byte[is32 ? 64 : 88];
                        BitConverter.GetBytes(1).CopyTo(item, 0);          // mask = LVIF_TEXT
                        BitConverter.GetBytes(i).CopyTo(item, 4);
                        BitConverter.GetBytes(sub).CopyTo(item, 8);
                        if (is32) { BitConverter.GetBytes((int)textAddr).CopyTo(item, 20); BitConverter.GetBytes(500).CopyTo(item, 24); }
                        else { BitConverter.GetBytes(textAddr).CopyTo(item, 24); BitConverter.GetBytes(500).CopyTo(item, 32); }
                        IntPtr w;
                        WriteProcessMemory(hp, mem, item, (IntPtr)item.Length, out w);
                        WriteProcessMemory(hp, (IntPtr)textAddr, new byte[1002], (IntPtr)1002, out w);
                        int n = (int)SendMessage(list, LVM_GETITEMTEXTW, (IntPtr)i, mem);
                        if (n > 0)
                        {
                            byte[] buf = new byte[1000];
                            IntPtr r;
                            ReadProcessMemory(hp, (IntPtr)textAddr, buf, (IntPtr)buf.Length, out r);
                            sb.Append(Encoding.Unicode.GetString(buf, 0, Math.Min(n * 2, 1000))).Append(" | ");
                        }
                    }
                    rows[i] = sb.ToString();
                }
                return rows.All(r => r.Length == 0) ? null : rows;
            }
            finally { VirtualFreeEx(hp, mem, IntPtr.Zero, 0x8000); }
        }
        finally { CloseHandle(hp); }
    }

    // ---------------------------------------------------------------- 창 찾기
    static List<IntPtr> Children(IntPtr h)
    {
        var l = new List<IntPtr>();
        EnumChildWindows(h, (c, _) => { l.Add(c); return true; }, IntPtr.Zero);
        return l;
    }

    static IntPtr FindDialogWithChild(string text)
    {
        IntPtr found = IntPtr.Zero;
        EnumWindows((h, _) =>
        {
            if (IsWindowVisible(h) && ClassOf(h) == "#32770" && OwnedByAxgate(h) &&
                Children(h).Any(c => TextOf(c).Contains(text))) { found = h; return false; }
            return true;
        }, IntPtr.Zero);
        return found;
    }

    static IntPtr FindTopByTitle(string title)
    {
        IntPtr found = IntPtr.Zero;
        EnumWindows((h, _) =>
        {
            if (IsWindowVisible(h) && TextOf(h).Contains(title) && OwnedByAxgate(h)) { found = h; return false; }
            return true;
        }, IntPtr.Zero);
        return found;
    }

    // AXGATE 가 띄운 창만 건드린다 (다른 프로그램의 «예» 버튼을 누르지 않게)
    static bool OwnedByAxgate(IntPtr h)
    {
        uint pid;
        GetWindowThreadProcessId(h, out pid);
        try { return Process.GetProcessById((int)pid).ProcessName.Equals("AxgateVpnClient", StringComparison.OrdinalIgnoreCase); }
        catch { return false; }
    }

    static bool ClickButton(IntPtr dlg, string startsWith)
    {
        foreach (var c in Children(dlg))
            if (ClassOf(c) == "Button" && TextOf(c).Replace("&", "").StartsWith(startsWith))
            {
                SendMessage(c, BM_CLICK, IntPtr.Zero, IntPtr.Zero);
                return true;
            }
        return false;
    }

    static string TextOf(IntPtr h)
    {
        int n = GetWindowTextLength(h);
        var sb = new StringBuilder(n + 2);
        GetWindowText(h, sb, n + 2);
        return sb.ToString();
    }

    static string ClassOf(IntPtr h)
    {
        var sb = new StringBuilder(256);
        GetClassName(h, sb, 256);
        return sb.ToString();
    }

    // ---------------------------------------------------------------- Win32
    const uint BM_CLICK = 0x00F5, WM_SETTEXT = 0x000C, WM_GETTEXTLENGTH = 0x000E, WM_CLOSE = 0x0010;
    const uint WM_KEYDOWN = 0x0100, WM_KEYUP = 0x0101, WM_CHAR = 0x0102;
    const uint LVM_GETITEMCOUNT = 0x1004, LVM_GETNEXTITEM = 0x100C, LVM_GETITEMTEXTW = 0x1073;
    const int LVNI_SELECTED = 2, VK_HOME = 0x24, VK_DOWN = 0x28;

    delegate bool EnumProc(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc f, IntPtr l);
    [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr p, EnumProc f, IntPtr l);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] static extern int GetWindowTextLength(IntPtr h);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern IntPtr SendMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern IntPtr SendMessage(IntPtr h, uint m, IntPtr w, string l);
    [DllImport("user32.dll")] static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("kernel32.dll")] static extern IntPtr OpenProcess(uint a, bool i, uint pid);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
    [DllImport("kernel32.dll")] static extern bool IsWow64Process(IntPtr h, out bool w);
    [DllImport("kernel32.dll")] static extern IntPtr VirtualAllocEx(IntPtr h, IntPtr a, IntPtr s, uint t, uint p);
    [DllImport("kernel32.dll")] static extern bool VirtualFreeEx(IntPtr h, IntPtr a, IntPtr s, uint t);
    [DllImport("kernel32.dll")] static extern bool WriteProcessMemory(IntPtr h, IntPtr a, byte[] b, IntPtr s, out IntPtr w);
    [DllImport("kernel32.dll")] static extern bool ReadProcessMemory(IntPtr h, IntPtr a, byte[] b, IntPtr s, out IntPtr r);

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct CREDENTIAL
    {
        public uint Flags, Type; public IntPtr TargetName, Comment;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
        public uint CredentialBlobSize; public IntPtr CredentialBlob; public uint Persist, AttributeCount;
        public IntPtr Attributes, TargetAlias, UserName;
    }
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool CredRead(string target, uint type, uint flags, out IntPtr cred);
    [DllImport("advapi32.dll")] static extern void CredFree(IntPtr p);
}
