namespace Wallet.Core;

public class Wallet : IWallet
{
    private int _amount = 12;

    public virtual int Balance() => _amount;

    public void Debit(int amount)
    {
        if (Balance() < amount)
        {
            throw new InvalidOperationException("Insufficient funds");
        }
        _amount -= amount;
        Ledger.Record($"debit {amount}");
    }
}
