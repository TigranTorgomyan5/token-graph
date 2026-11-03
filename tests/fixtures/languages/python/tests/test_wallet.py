from wallet.wallet import BonusWallet


def test_debit_reduces_balance():
    wallet = BonusWallet()
    wallet.debit(3)
    assert wallet.balance() == 14
