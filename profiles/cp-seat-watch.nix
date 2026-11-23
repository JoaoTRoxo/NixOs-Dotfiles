{ config, inputs, ... }:

# CP Seat Watch interactive Telegram bot (@aureaTrain_bot, JoaoTRoxo/cp-seat-watch):
# starts idle and waits for Telegram commands to list trains for a day, swap/change routes,
# probe seats, or watch & auto-reserve Green Rail Pass seats.
# To update it: nix flake update cp-seat-watch, then rebuild.
{
  imports = [ "${inputs.cp-seat-watch}/packaging/nix/service.nix" ];

  # Keep the source out of garbage collection: root can't fetch the private
  # repo again (as pulse's, treino's and jrx's)
  system.extraDependencies = [ inputs.cp-seat-watch ];

  age.secrets.cp-seat-watch.file = ../secrets/cp-seat-watch.age;

  services.cp-seat-watch = {
    enable = true;
    mode = "bot";
    environmentFile = config.age.secrets.cp-seat-watch.path;
    settings = {
      journey = {
        origin = "Coimbra-B";
        destination = "Lisboa Santa Apolonia";
        green_pass = true;
        travel_class = 2;
        passengers = 1;
      };
      poll = {
        interval_seconds = 7;
        jitter_seconds = 1;
        active_hours = [ "06:30" "23:30" ];
      };
      language = "en";
    };
  };
}
