using System.Collections.Generic;

namespace MiniPlant.Core
{
    /// <summary>Partial class: the other half is in Recipe.Validation.cs.</summary>
    public partial class Recipe
    {
        private readonly List<double> _points = new List<double>();

        public Recipe(string name)
        {
            Name = name;
        }

        public string Name { get; }

        public IReadOnlyList<double> Points => _points;

        public void AddPoint(double mm)
        {
            _points.Add(mm);
        }
    }
}
