package com.example.wallet;

public interface IWallet {
    int balance();

    void debit(int amount);
}
