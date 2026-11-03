using Wallet.Core;

namespace Wallet.Tests;

public class WalletTests
{
    public void Debit_reduces_balance()
    {
        var wallet = new BonusWallet();
        wallet.Debit(3);
        _ = wallet.Balance();
    }
}
