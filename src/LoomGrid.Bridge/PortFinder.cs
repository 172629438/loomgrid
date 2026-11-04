using System.Net;
using System.Net.Sockets;

namespace LoomGrid.Bridge;

/// <summary>
/// 端口选择：优先用期望端口，被占则顺序向后探测。
/// 用户不关心端口号是多少 —— 只要能打开就行。
/// </summary>
public static class PortFinder
{
    public static int FindFree(int preferred, int maxProbe = 40)
    {
        for (int i = 0; i < maxProbe; i++)
        {
            int port = preferred + i;
            if (port > 65535) break;
            if (IsFree(port)) return port;
        }
        // 全部被占：让系统分配一个随机空闲端口
        var l = new TcpListener(IPAddress.Loopback, 0);
        l.Start();
        int assigned = ((IPEndPoint)l.LocalEndpoint).Port;
        l.Stop();
        return assigned;
    }

    public static bool IsFree(int port)
    {
        TcpListener? l = null;
        try
        {
            l = new TcpListener(IPAddress.Loopback, port);
            l.Start();
            return true;
        }
        catch (SocketException)
        {
            return false;
        }
        finally
        {
            try { l?.Stop(); } catch { }
        }
    }
}
