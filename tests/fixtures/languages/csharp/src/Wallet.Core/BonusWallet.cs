namespace Wallet.Core;

public class BonusWallet : Wallet
{
    public int Bonus { get; init; } = 5;

    public override int Balance() => base.Balance() + Bonus;
}
