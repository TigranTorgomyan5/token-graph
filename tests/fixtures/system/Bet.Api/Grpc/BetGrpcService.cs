using Grpc.Core;
using MassTransit;
using NATS.Client.Core;

public class BetGrpcService : BetService.BetServiceBase { }

public class SettlementConsumer : IConsumer<SettlementRequested>
{
    public Task Consume(ConsumeContext<SettlementRequested> context) => Task.CompletedTask;
}

public class BetListener
{
    public async Task Run(INatsConnection nats)
    {
        await foreach (var msg in nats.SubscribeAsync<string>("bets.placed")) { }
    }
}
