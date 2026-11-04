import { Controller, Get, Post, Body } from '@nestjs/common';

@Controller('auth')
export class AuthController {
  constructor(private readonly http: HttpService) {}

  @Post('login')
  async login(@Body() body: LoginDto) {
    return this.http.get('/users/lookup');
  }

  @Get()
  status() {
    return 'ok';
  }
}

export class LoginDto {
  phone: string;
  password?: string;
}
