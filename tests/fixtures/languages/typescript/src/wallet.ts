import { record } from './ledger';

export interface IWallet {
  balance(): number;
  debit(amount: number): void;
}

export class Wallet implements IWallet {
  protected amount = 12;

  balance(): number {
    return this.amount;
  }

  debit(amount: number): void {
    if (this.balance() < amount) {
      throw new Error('Insufficient funds');
    }
    this.amount -= amount;
    record(`debit ${amount}`);
  }
}
