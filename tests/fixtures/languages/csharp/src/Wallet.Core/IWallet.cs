namespace Wallet.Core;

public interface IWallet
{
    int Balance();
    void Debit(int amount);
}
