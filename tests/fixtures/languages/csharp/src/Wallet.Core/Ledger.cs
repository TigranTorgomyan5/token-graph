namespace Wallet.Core;

public static class Ledger
{
    private static readonly List<string> Entries = new();

    public static void Record(string entry) => Entries.Add(entry);
}
