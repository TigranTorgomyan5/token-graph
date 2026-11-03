package com.example.wallet;

public class WalletTest {
    public void debitReducesBalance() {
        BonusWallet wallet = new BonusWallet();
        wallet.debit(3);
        if (wallet.balance() != 14) {
            throw new AssertionError("unexpected balance");
        }
    }
}
