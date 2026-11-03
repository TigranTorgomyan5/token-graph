using NATS.Client.Core;
using MassTransit;

namespace Adastra;

public static class Subjects
{
    public const string BetPlaced = "bets.placed";
}

public class BetPublisher
{
    private readonly INatsConnection _nats;
    private readonly IPublishEndpoint _bus;
    private readonly HttpClient _http;

    public async Task Place(Guid id, string market)
    {
        await _nats.PublishAsync(Subjects.BetPlaced, new { id });
        await _nats.PublishAsync($"wallet.{market}.debit", new { id });
        await _bus.Publish(new SettlementRequested { BetId = id });
        var bet = await _http.GetFromJsonAsync<BetDto>($"api/bets/{id}");
    }
}
