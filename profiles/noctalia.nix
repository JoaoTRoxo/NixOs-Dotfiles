{ pkgs, inputs, ... }:
{
  home-manager.users.simaolavos = {
    # import the home manager module
    imports = [
      inputs.noctalia.homeModules.default
    ];

    
    # configure options
    programs.noctalia-shell = {
      enable = true;

      settings = {
        # configure noctalia here
        bar = {
          density = "compact";
          position = "top";
          showCapsule = false;
          widgets = {
            left = [
              {
                id = "ControlCenter";
                useDistroLogo = true;
              }
              {
                id = "Network";
              }
              {
                id = "Bluetooth";
              }
            ];
            center = [
              {
                hideUnoccupied = false;
                id = "Workspace";
                labelMode = "none";
              }
            ];
            right = [
              {
                alwaysShowPercentage = true;
                id = "Battery";
                warningThreshold = 30;
              }
              {
                formatHorizontal = "HH:mm";
                formatVertical = "HH mm";
                id = "Clock";
                useMonospacedFont = true;
                usePrimaryColor = true;
              }
            ];
          };
        };
        plugins = {
          sources = [
            {
              enabled = true;
              name = "Official Noctalia Plugins";
              url = "https://github.com/noctalia-dev/noctalia-plugins";

            }
          ];

          states = {
            battery-threshold = {
            enabled = true;
            sourceUrl = "https://github.com/noctalia-dev/noctalia-plugins/tree/main/battery-threshold";
            };
          };
        };

        pluginSettings = {
          battery-threshold = {
            minimumThreshold = 25;
            hideBackground = true;
          };
        };
        colorSchemes.predefinedScheme = "Monochrome";
        general = {
          avatarImage = "";
          radiusRatio = 0.2;
        };
        location = {
          monthBeforeDay = true;
          name = "Lisbon, Portugal";
        };
      };
      # this may also be a string or a path to a JSON file.
    };
  };
}
