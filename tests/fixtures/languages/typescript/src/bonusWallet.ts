import { Wallet } from './wallet';

export class BonusWallet extends Wallet {
  bonus = 5;

  balance(): number {
    return super.balance() + this.bonus;
  }
}
